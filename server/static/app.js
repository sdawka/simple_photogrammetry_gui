(() => {
  "use strict";

  const API = "/api/v1";
  const POLL_MS = 2000;
  const ACTIVE_STATES = new Set(["uploading", "queued", "running"]);
  const TERMINAL_STATES = new Set(["completed", "failed", "cancelled", "interrupted"]);

  const $ = (selector) => document.querySelector(selector);
  const elements = {
    connection: $("#connection"),
    connectionLabel: $("#connection-label"),
    form: $("#job-form"),
    toggleCreate: $("#toggle-create"),
    name: $("#job-name"),
    quality: $("#quality"),
    qualityField: $("#quality-field"),
    dropZone: $("#drop-zone"),
    photoInput: $("#photo-input"),
    dropTitle: $("#drop-title"),
    fileSummary: $("#file-summary"),
    fileCount: $("#file-count"),
    fileSize: $("#file-size"),
    fileList: $("#file-list"),
    clearFiles: $("#clear-files"),
    submit: $("#submit-job"),
    formMessage: $("#form-message"),
    uploadProgress: $("#upload-progress"),
    uploadLabel: $("#upload-label"),
    uploadPercent: $("#upload-percent"),
    uploadBar: $("#upload-bar"),
    jobsList: $("#jobs-list"),
    jobsEmpty: $("#jobs-empty"),
    refreshJobs: $("#refresh-jobs"),
    detailPlaceholder: $("#detail-placeholder"),
    detailContent: $("#detail-content"),
    detailName: $("#detail-name"),
    detailStatus: $("#detail-status"),
    detailMeta: $("#detail-meta"),
    cancelJob: $("#cancel-job"),
    stageLabel: $("#stage-label"),
    jobPercent: $("#job-percent"),
    jobBar: $("#job-bar"),
    resultSection: $("#result-section"),
    artifactList: $("#artifact-list"),
    preview: $("#preview"),
    previewImage: $("#preview-image"),
    previewFallback: $("#preview-fallback"),
    jobLogs: $("#job-logs"),
    followLogs: $("#follow-logs"),
    copyLogs: $("#copy-logs"),
    toast: $("#toast"),
  };

  const state = {
    files: [],
    jobs: [],
    selectedId: null,
    selectedJob: null,
    logsFor: null,
    logCursor: 0,
    logText: "",
    polling: false,
    uploading: false,
    pollTimer: null,
    toastTimer: null,
  };

  function setConnection(online, label) {
    elements.connection.dataset.state = online ? "online" : "offline";
    elements.connectionLabel.textContent = label || (online ? "Server online" : "Server unavailable");
  }

  async function api(path, options = {}) {
    const response = await fetch(`${API}${path}`, {
      cache: "no-store",
      ...options,
      headers: { Accept: "application/json", ...(options.headers || {}) },
    });
    if (!response.ok) {
      let reason = `${response.status} ${response.statusText}`;
      try {
        const body = await response.json();
        reason = body.error || body.message || reason;
      } catch (_) { /* response was not JSON */ }
      throw new Error(reason);
    }
    if (response.status === 204) return null;
    const contentType = response.headers.get("content-type") || "";
    return contentType.includes("application/json") ? response.json() : response.text();
  }

  function unwrapJob(payload) {
    return payload && payload.job ? payload.job : payload;
  }

  function unwrapList(payload, key) {
    if (Array.isArray(payload)) return payload;
    return payload && Array.isArray(payload[key]) ? payload[key] : [];
  }

  function normalizedProgress(value) {
    const number = Number(value);
    if (!Number.isFinite(number)) return null;
    return Math.max(0, Math.min(1, number > 1 ? number / 100 : number));
  }

  function jobProgress(job) {
    const direct = normalizedProgress(job && job.progress);
    if (direct !== null) return direct;
    const current = Number(job && (job.stage_index ?? job.stageIndex));
    const total = Number(job && (job.stage_total ?? job.stageTotal));
    if (!Number.isFinite(current) || !Number.isFinite(total) || total <= 0) return null;
    return Math.max(0, Math.min(1, current / total));
  }

  function stageText(job) {
    if (!job) return "Waiting";
    if (typeof job.stage === "string" && job.stage) return job.stage;
    if (job.stage && (job.stage.label || job.stage.name)) return job.stage.label || job.stage.name;
    const fallbacks = {
      uploading: "Receiving photos",
      queued: "Waiting for GPU",
      running: "Reconstructing",
      completed: "Complete",
      failed: "Failed",
      cancelled: "Cancelled",
      interrupted: "Interrupted by server restart",
    };
    return fallbacks[job.state] || "Waiting";
  }

  function stateLabel(value) {
    return String(value || "unknown").replaceAll("_", " ");
  }

  function formatBytes(bytes) {
    const value = Number(bytes);
    if (!Number.isFinite(value) || value < 0) return "";
    const units = ["B", "KB", "MB", "GB", "TB"];
    let size = value;
    let unit = 0;
    while (size >= 1024 && unit < units.length - 1) { size /= 1024; unit += 1; }
    const precision = unit === 0 || size >= 10 ? 0 : 1;
    return `${size.toFixed(precision)} ${units[unit]}`;
  }

  function formatDate(value) {
    if (!value) return "";
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
  }

  function showToast(message) {
    window.clearTimeout(state.toastTimer);
    elements.toast.textContent = message;
    elements.toast.hidden = false;
    state.toastTimer = window.setTimeout(() => { elements.toast.hidden = true; }, 3500);
  }

  function setUploadProgress(percent, label) {
    const safe = Math.max(0, Math.min(100, percent));
    elements.uploadProgress.hidden = false;
    elements.uploadBar.style.width = `${safe}%`;
    elements.uploadPercent.textContent = `${Math.round(safe)}%`;
    elements.uploadLabel.textContent = label;
  }

  function uniquePhotoFiles(incoming) {
    const allowed = new Set(["image/jpeg", "image/png"]);
    const result = [];
    const names = new Set();
    for (const file of incoming) {
      const extensionOkay = /\.(jpe?g|png)$/i.test(file.name);
      if (!(allowed.has(file.type) || extensionOkay)) continue;
      const key = file.name.toLocaleLowerCase();
      if (names.has(key)) continue;
      names.add(key);
      result.push(file);
    }
    return result;
  }

  function updateFiles(files) {
    state.files = uniquePhotoFiles(files);
    elements.fileList.replaceChildren();
    elements.fileSummary.hidden = state.files.length === 0;
    elements.dropTitle.textContent = state.files.length ? "Choose a different photo set" : "Choose photos or drop them here";
    if (!state.files.length) return;

    const total = state.files.reduce((sum, file) => sum + file.size, 0);
    elements.fileCount.textContent = `${state.files.length} photo${state.files.length === 1 ? "" : "s"}`;
    elements.fileSize.textContent = formatBytes(total);
    for (const file of state.files.slice(0, 12)) {
      const item = document.createElement("li");
      item.textContent = file.name;
      item.title = file.name;
      elements.fileList.append(item);
    }
    if (state.files.length > 12) {
      const extra = document.createElement("li");
      extra.textContent = `+${state.files.length - 12} more`;
      elements.fileList.append(extra);
    }
  }

  function selectedKind() {
    return elements.form.elements.kind.value;
  }

  function updateKind() {
    const isMesh = selectedKind() === "mesh";
    elements.qualityField.hidden = !isMesh;
    if (!isMesh) elements.quality.value = "medium";
  }

  function validateSubmission() {
    const name = elements.name.value.trim();
    if (!name) return "Give this job a name.";
    if (state.files.length < 3) return "Choose at least 3 JPG or PNG photos.";
    return "";
  }

  function putFile(jobId, file, onProgress) {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      const encodedName = encodeURIComponent(file.name);
      xhr.open("PUT", `${API}/jobs/${encodeURIComponent(jobId)}/images/${encodedName}`);
      xhr.setRequestHeader("Content-Type", file.type || "application/octet-stream");
      xhr.setRequestHeader("Accept", "application/json");
      xhr.upload.addEventListener("progress", (event) => {
        if (event.lengthComputable) onProgress(event.loaded);
      });
      xhr.addEventListener("load", () => {
        if (xhr.status >= 200 && xhr.status < 300) resolve();
        else {
          let reason = `${xhr.status} ${xhr.statusText}`;
          try {
            const payload = JSON.parse(xhr.responseText);
            reason = payload.error || payload.message || reason;
          } catch (_) { /* response was not JSON */ }
          reject(new Error(reason));
        }
      });
      xhr.addEventListener("error", () => reject(new Error("Network connection lost")));
      xhr.addEventListener("abort", () => reject(new Error("Upload cancelled")));
      xhr.send(file);
    });
  }

  async function submitJob(event) {
    event.preventDefault();
    if (state.uploading) return;
    const validation = validateSubmission();
    elements.formMessage.textContent = validation;
    if (validation) return;

    state.uploading = true;
    elements.submit.disabled = true;
    elements.submit.textContent = "Uploading…";
    const totalBytes = state.files.reduce((sum, file) => sum + file.size, 0);
    let completedBytes = 0;

    try {
      setUploadProgress(0, "Creating job…");
      const createdPayload = await api("/jobs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: elements.name.value.trim(),
          kind: selectedKind(),
          quality: elements.quality.value,
        }),
      });
      const created = unwrapJob(createdPayload);
      if (!created || !created.id) throw new Error("Server did not return a job id");
      state.selectedId = created.id;

      for (let index = 0; index < state.files.length; index += 1) {
        const file = state.files[index];
        const label = `Uploading ${index + 1} of ${state.files.length}: ${file.name}`;
        await putFile(created.id, file, (loaded) => {
          const percent = totalBytes ? ((completedBytes + loaded) / totalBytes) * 96 : 0;
          setUploadProgress(percent, label);
        });
        completedBytes += file.size;
      }

      setUploadProgress(98, "Adding job to the queue…");
      await api(`/jobs/${encodeURIComponent(created.id)}/start`, { method: "POST" });
      setUploadProgress(100, "Upload complete · job queued");
      elements.form.reset();
      updateFiles([]);
      updateKind();
      elements.formMessage.textContent = "";
      showToast("Job uploaded and queued.");
      await refreshJobs(true);
      window.setTimeout(() => { elements.uploadProgress.hidden = true; }, 1800);
    } catch (error) {
      elements.formMessage.textContent = `Upload stopped: ${error.message}. Your partial job remains on the server.`;
      showToast("Upload stopped. Check the connection and try again.");
      await refreshJobs(false);
    } finally {
      state.uploading = false;
      elements.submit.disabled = false;
      elements.submit.textContent = "Create job";
    }
  }

  function jobButton(job) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `job-row${job.id === state.selectedId ? " selected" : ""}`;
    button.dataset.jobId = job.id;

    const top = document.createElement("div");
    top.className = "job-row-top";
    const name = document.createElement("strong");
    name.textContent = job.name || `Job ${job.id}`;
    const badge = document.createElement("span");
    badge.className = "status-badge";
    badge.dataset.state = job.state || "unknown";
    badge.textContent = stateLabel(job.state);
    top.append(name, badge);

    const meta = document.createElement("div");
    meta.className = "job-row-meta";
    const kind = document.createElement("span");
    kind.textContent = job.kind === "splat" ? "Gaussian splat" : "Mesh";
    const created = document.createElement("span");
    created.textContent = formatDate(job.created_at || job.createdAt);
    meta.append(kind, created);

    const progress = document.createElement("div");
    progress.className = "mini-progress";
    const fill = document.createElement("span");
    const value = jobProgress(job);
    fill.style.width = `${value === null ? 0 : value * 100}%`;
    progress.append(fill);
    button.append(top, meta, progress);
    button.addEventListener("click", () => selectJob(job.id));
    return button;
  }

  function renderJobs() {
    elements.jobsList.replaceChildren(...state.jobs.map(jobButton));
    elements.jobsEmpty.hidden = state.jobs.length > 0;
  }

  async function refreshJobs(selectDefault = false) {
    try {
      const payload = await api("/jobs");
      state.jobs = unwrapList(payload, "jobs");
      setConnection(true, "Server online");
      if ((selectDefault || !state.selectedId) && state.jobs.length) state.selectedId = state.jobs[0].id;
      if (state.selectedId && !state.jobs.some((job) => job.id === state.selectedId)) state.selectedId = state.jobs[0]?.id || null;
      renderJobs();
      if (state.selectedId) await refreshSelectedJob();
      else showNoSelection();
    } catch (error) {
      setConnection(false, "Server unavailable");
      if (!state.jobs.length) elements.jobsEmpty.hidden = false;
    }
  }

  async function selectJob(jobId) {
    if (state.selectedId !== jobId) {
      state.selectedId = jobId;
      state.logsFor = null;
      state.logCursor = 0;
      state.logText = "";
      elements.jobLogs.textContent = "Loading logs…";
    }
    renderJobs();
    await refreshSelectedJob();
  }

  function showNoSelection() {
    elements.detailPlaceholder.hidden = false;
    elements.detailContent.hidden = true;
    state.selectedJob = null;
  }

  function jobMetadata(job) {
    const parts = [job.kind === "splat" ? "Gaussian splat" : "Textured mesh"];
    if (job.quality) parts.push(`${job.quality} quality`);
    const count = job.uploaded_images ?? job.photo_count ?? job.photoCount ?? job.image_count;
    if (Number.isFinite(Number(count))) parts.push(`${count} photos`);
    const created = formatDate(job.created_at || job.createdAt);
    if (created) parts.push(`created ${created}`);
    return parts.join(" · ");
  }

  function renderDetail(job) {
    state.selectedJob = job;
    elements.detailPlaceholder.hidden = true;
    elements.detailContent.hidden = false;
    elements.detailName.textContent = job.name || `Job ${job.id}`;
    elements.detailStatus.textContent = stateLabel(job.state);
    elements.detailStatus.dataset.state = job.state || "unknown";
    elements.detailMeta.textContent = jobMetadata(job);
    elements.cancelJob.hidden = !ACTIVE_STATES.has(job.state);
    elements.cancelJob.disabled = job.state === "uploading" && state.uploading;
    elements.stageLabel.textContent = stageText(job);

    const progress = jobProgress(job);
    elements.jobBar.style.width = `${progress === null ? 0 : progress * 100}%`;
    elements.jobPercent.textContent = progress === null ? "—" : `${Math.round(progress * 100)}%`;
    elements.resultSection.hidden = job.state !== "completed";
    if (job.error && TERMINAL_STATES.has(job.state)) {
      elements.stageLabel.textContent = `${stageText(job)} · ${job.error}`;
    }
  }

  async function refreshSelectedJob() {
    if (!state.selectedId) return;
    const selectedAtStart = state.selectedId;
    try {
      const payload = await api(`/jobs/${encodeURIComponent(selectedAtStart)}`);
      if (selectedAtStart !== state.selectedId) return;
      const job = unwrapJob(payload);
      renderDetail(job);
      const listIndex = state.jobs.findIndex((entry) => entry.id === job.id);
      if (listIndex >= 0) state.jobs[listIndex] = { ...state.jobs[listIndex], ...job };
      renderJobs();
      await Promise.all([refreshLogs(job), job.state === "completed" ? refreshArtifacts(job) : Promise.resolve()]);
    } catch (error) {
      if (selectedAtStart === state.selectedId) elements.stageLabel.textContent = `Unable to refresh: ${error.message}`;
    }
  }

  async function refreshLogs(job) {
    if (state.logsFor !== job.id) {
      state.logsFor = job.id;
      state.logCursor = 0;
      state.logText = "";
    }
    try {
      const payload = await api(`/jobs/${encodeURIComponent(job.id)}/logs?after=${state.logCursor}`);
      let addition = "";
      let next = state.logCursor;
      if (typeof payload === "string") {
        addition = payload;
        next += addition ? addition.split("\n").length : 0;
      } else if (payload) {
        if (Array.isArray(payload.lines)) addition = payload.lines.join("\n") + (payload.lines.length ? "\n" : "");
        else addition = payload.text || "";
        next = Number(payload.next ?? payload.next_line ?? payload.cursor ?? next);
      }
      if (addition) state.logText += addition;
      if (Number.isFinite(next)) state.logCursor = next;
      elements.jobLogs.textContent = state.logText || "Waiting for log output…";
      if (elements.followLogs.checked) elements.jobLogs.scrollTop = elements.jobLogs.scrollHeight;
    } catch (_) { /* keep the last available log while the job endpoint recovers */ }
  }

  function artifactUrl(jobId, artifact) {
    if (artifact.url) return artifact.url;
    const name = artifact.name || artifact.id;
    return `${API}/jobs/${encodeURIComponent(jobId)}/artifacts/${encodeURIComponent(name)}`;
  }

  async function refreshArtifacts(job) {
    try {
      const payload = await api(`/jobs/${encodeURIComponent(job.id)}/artifacts`);
      const artifacts = unwrapList(payload, "artifacts");
      renderArtifacts(job, artifacts);
    } catch (_) {
      elements.artifactList.replaceChildren();
    }
  }

  function renderArtifacts(job, artifacts) {
    const links = artifacts.map((artifact) => {
      if (typeof artifact === "string") artifact = { name: artifact };
      const url = artifactUrl(job.id, artifact);
      const link = document.createElement("a");
      link.className = "artifact";
      link.href = url;
      link.download = artifact.name || "";
      const copy = document.createElement("div");
      const label = document.createElement("strong");
      label.textContent = artifact.label || artifact.name || artifact.id || "Result file";
      const meta = document.createElement("small");
      meta.textContent = [artifact.kind || artifact.mime || "file", formatBytes(artifact.size ?? artifact.bytes)].filter(Boolean).join(" · ");
      copy.append(label, meta);
      const arrow = document.createElement("span");
      arrow.textContent = "↓";
      link.append(copy, arrow);
      return link;
    });
    elements.artifactList.replaceChildren(...links);

    const image = artifacts.find((artifact) => {
      if (typeof artifact === "string") return /\.(png|jpe?g|webp)$/i.test(artifact);
      return artifact.kind === "thumbnail" || String(artifact.mime || "").startsWith("image/") || /\.(png|jpe?g|webp)$/i.test(artifact.name || "");
    });
    if (image) {
      const normalized = typeof image === "string" ? { name: image } : image;
      elements.previewImage.src = artifactUrl(job.id, normalized);
      elements.preview.hidden = false;
      elements.previewFallback.hidden = true;
    } else {
      elements.preview.hidden = true;
      elements.previewImage.removeAttribute("src");
      elements.previewFallback.hidden = false;
    }
  }

  async function cancelSelectedJob() {
    const job = state.selectedJob;
    if (!job || !ACTIVE_STATES.has(job.state)) return;
    if (!window.confirm(`Cancel “${job.name || job.id}”? Partial files will remain until server cleanup.`)) return;
    elements.cancelJob.disabled = true;
    try {
      await api(`/jobs/${encodeURIComponent(job.id)}/cancel`, { method: "POST" });
      showToast("Cancellation requested.");
      await refreshJobs(false);
    } catch (error) {
      showToast(`Could not cancel: ${error.message}`);
    } finally {
      elements.cancelJob.disabled = false;
    }
  }

  async function copyLogs() {
    try {
      await navigator.clipboard.writeText(state.logText);
      showToast("Log copied.");
    } catch (_) {
      showToast("Clipboard access is unavailable in this browser.");
    }
  }

  function bindEvents() {
    elements.form.addEventListener("submit", submitJob);
    elements.form.addEventListener("change", (event) => { if (event.target.name === "kind") updateKind(); });
    elements.dropZone.addEventListener("click", () => elements.photoInput.click());
    elements.dropZone.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") { event.preventDefault(); elements.photoInput.click(); }
    });
    elements.photoInput.addEventListener("change", () => updateFiles([...elements.photoInput.files]));
    elements.clearFiles.addEventListener("click", () => { elements.photoInput.value = ""; updateFiles([]); });

    for (const eventName of ["dragenter", "dragover"]) {
      elements.dropZone.addEventListener(eventName, (event) => { event.preventDefault(); elements.dropZone.classList.add("dragging"); });
    }
    for (const eventName of ["dragleave", "drop"]) {
      elements.dropZone.addEventListener(eventName, (event) => { event.preventDefault(); elements.dropZone.classList.remove("dragging"); });
    }
    elements.dropZone.addEventListener("drop", (event) => updateFiles([...event.dataTransfer.files]));
    elements.refreshJobs.addEventListener("click", () => refreshJobs(false));
    elements.cancelJob.addEventListener("click", cancelSelectedJob);
    elements.copyLogs.addEventListener("click", copyLogs);
    elements.toggleCreate.addEventListener("click", () => {
      const hidden = elements.form.hidden;
      elements.form.hidden = !hidden;
      elements.toggleCreate.textContent = hidden ? "Hide setup" : "Show setup";
      elements.toggleCreate.setAttribute("aria-expanded", String(hidden));
    });
    document.addEventListener("visibilitychange", () => {
      if (!document.hidden) refreshJobs(false);
    });
  }

  async function poll() {
    if (state.polling || document.hidden) return;
    state.polling = true;
    try { await refreshJobs(false); } finally { state.polling = false; }
  }

  bindEvents();
  updateKind();
  poll();
  state.pollTimer = window.setInterval(poll, POLL_MS);
})();
