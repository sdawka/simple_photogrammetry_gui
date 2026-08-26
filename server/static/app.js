(() => {
  "use strict";

  const API = "/api/v1";
  const POLL_MS = 2000;
  const ACTIVE_STATES = new Set(["uploading", "queued", "running"]);
  const TERMINAL_STATES = new Set(["completed", "failed", "cancelled", "interrupted"]);
  const VIEW_NAMES = new Set(["result", "activity", "files"]);
  const CONFIG = Object.freeze({
    viewerMemoryWarningBytes: window.PHOTOGRAMMETRY_CONFIG?.viewerMemoryWarningBytes ?? 200 * 1024 * 1024,
    viewerSlowMs: window.PHOTOGRAMMETRY_CONFIG?.viewerSlowMs ?? 10000,
    viewerTimeoutMs: window.PHOTOGRAMMETRY_CONFIG?.viewerTimeoutMs ?? 45000,
  });
  const $ = (selector) => document.querySelector(selector);

  const elements = {
    connection: $("#connection"), connectionState: $("#connection-state"), connectionLabel: $("#connection-label"),
    newReconstruction: $("#new-reconstruction"), emptyNew: $("#empty-new"), queueRail: $("#queue-rail"), queueSummary: $("#queue-summary"),
    jobsList: $("#jobs-list"), jobsEmpty: $("#jobs-empty"), refreshJobs: $("#refresh-jobs"), backToJobs: $("#back-to-jobs"),
    detailPlaceholder: $("#detail-placeholder"), jobNotFound: $("#job-not-found"), detailContent: $("#detail-content"),
    detailName: $("#detail-name"), detailStatus: $("#detail-status"), detailMeta: $("#detail-meta"),
    stageList: $("#stage-list"), stageLabel: $("#stage-label"), jobPercent: $("#job-percent"), jobBar: $("#job-bar"),
    diagnostic: $("#capture-diagnostic"), diagnosticTitle: $("#diagnostic-title"), diagnosticFacts: $("#diagnostic-facts"), diagnosticCopy: $("#diagnostic-copy"),
    diagnosticTips: $("#diagnostic-tips"), captureTips: $("#capture-tips"), closeCaptureTips: $("#close-capture-tips"),
    tabs: [...document.querySelectorAll("[role=tab][data-view]")], views: [...document.querySelectorAll(".job-view")],
    preview: $("#preview"), previewImage: $("#preview-image"), previewFallback: $("#preview-fallback"), resultMetadata: $("#result-metadata"),
    splatViewer: $("#splat-viewer"), splatViewerLabel: $("#splat-viewer-label"), viewerFileMeta: $("#viewer-file-meta"),
    viewerFrameShell: $("#viewer-frame-shell"), splatViewerFrame: $("#splat-viewer-frame"), viewerActivation: $("#viewer-activation"),
    activateViewer: $("#activate-viewer"), exitViewer: $("#exit-viewer"), resetCamera: $("#reset-camera"),
    viewerControls: $("#viewer-controls"), controlsHelp: $("#controls-help"), fullscreenViewer: $("#fullscreen-viewer"),
    viewerStatus: $("#viewer-status"), downloadSplat: $("#download-splat"),
    activityState: $("#activity-state"), activityGuidance: $("#activity-guidance"), cancelJob: $("#cancel-job"),
    followLogs: $("#follow-logs"), copyLogs: $("#copy-logs"), jobLogs: $("#job-logs"),
    artifactList: $("#artifact-list"), filesEmpty: $("#files-empty"),
    drawer: $("#new-drawer"), form: $("#job-form"), closeDrawer: $("#close-drawer"), cancelCreate: $("#cancel-create"),
    name: $("#job-name"), quality: $("#quality"), qualityField: $("#quality-field"), dropZone: $("#drop-zone"), photoInput: $("#photo-input"),
    matcherInputs: [...document.querySelectorAll('input[name="feature_matcher"]')], matcherNote: $("#matching-method-note"),
    dropTitle: $("#drop-title"), fileSummary: $("#file-summary"), fileCount: $("#file-count"), fileSize: $("#file-size"),
    fileList: $("#file-list"), fileErrors: $("#file-errors"), clearFiles: $("#clear-files"), submit: $("#submit-job"),
    formMessage: $("#form-message"), uploadProgress: $("#upload-progress"), uploadLabel: $("#upload-label"),
    uploadPercent: $("#upload-percent"), uploadBar: $("#upload-bar"),
    toast: $("#toast"), toastCopy: $("#toast-copy"), dismissToast: $("#dismiss-toast"), announcer: $("#app-announcer"),
  };

  const state = {
    online: null,
    files: [], rejectedFiles: [], objectUrls: [], jobs: [], selectedId: null, selectedJob: null, currentView: null,
    logsFor: null, logCursor: 0, logText: "", artifacts: [], artifactsFor: null, artifactsFingerprint: "",
    polling: false, uploading: false, resumeJobId: null, toastTimer: null, splatViewerFor: null, viewerUrl: null,
    viewerActive: false, viewerReady: false, viewerResetting: false, viewerFullscreen: false,
    viewerLoadFailed: false, viewerLoadToken: 0, viewerSlowTimer: null, viewerTimeoutTimer: null,
    viewerResumeAfterReset: false, viewerDocument: null, viewerHandlers: null,
    previousJobState: null, previousStage: null, initialUrlHadJob: false,
  };

  function announce(message) {
    elements.announcer.textContent = "";
    window.setTimeout(() => { elements.announcer.textContent = message; }, 20);
  }

  function showToast(message) {
    window.clearTimeout(state.toastTimer);
    elements.toastCopy.textContent = message;
    elements.toast.hidden = false;
    state.toastTimer = window.setTimeout(() => { elements.toast.hidden = true; }, 6000);
  }

  function updateUrl(patch, mode = "push") {
    const url = new URL(window.location.href);
    Object.entries(patch).forEach(([key, value]) => {
      if (value === null || value === undefined || value === "") url.searchParams.delete(key);
      else url.searchParams.set(key, String(value));
    });
    history[mode === "replace" ? "replaceState" : "pushState"]({}, "", `${url.pathname}${url.search}${url.hash}`);
  }

  function defaultView(job) { return job?.state === "completed" ? "result" : "activity"; }

  function setConnection(online, label) {
    const changed = state.online !== online;
    state.online = online;
    elements.connection.dataset.state = online ? "online" : "offline";
    elements.connectionState.textContent = online ? "ONLINE" : "OFFLINE";
    elements.connectionLabel.textContent = label || (online ? "Server online" : "Server unavailable");
    syncCreateControls();
    if (changed && online === false) announce("Server unavailable. Existing results remain available in this page.");
  }

  async function api(path, options = {}) {
    const response = await fetch(`${API}${path}`, { cache: "no-store", ...options, headers: { Accept: "application/json", ...(options.headers || {}) } });
    if (!response.ok) {
      let reason = `${response.status} ${response.statusText}`;
      let code = "";
      try {
        const body = await response.json();
        reason = body.error || body.message || reason;
        code = body.code || "";
      } catch (_) { /* response was not JSON */ }
      const error = new Error(reason);
      error.code = code;
      error.status = response.status;
      throw error;
    }
    if (response.status === 204) return null;
    const contentType = response.headers.get("content-type") || "";
    return contentType.includes("application/json") ? response.json() : response.text();
  }

  function unwrapJob(payload) { return payload?.job || payload; }
  function unwrapList(payload, key) { return Array.isArray(payload) ? payload : (Array.isArray(payload?.[key]) ? payload[key] : []); }

  function normalizedProgress(value) {
    const number = Number(value);
    if (!Number.isFinite(number)) return null;
    return Math.max(0, Math.min(1, number > 1 ? number / 100 : number));
  }

  function jobProgress(job) {
    const direct = normalizedProgress(job?.progress);
    if (direct !== null) return direct;
    const current = Number(job?.stage_index ?? job?.stageIndex);
    const total = Number(job?.stage_total ?? job?.stageTotal);
    return Number.isFinite(current) && Number.isFinite(total) && total > 0 ? Math.max(0, Math.min(1, current / total)) : null;
  }

  function stageText(job) {
    if (typeof job?.stage === "string" && job.stage) return job.stage;
    if (job?.stage?.label || job?.stage?.name) return job.stage.label || job.stage.name;
    return { uploading: "Receiving photos", queued: "Waiting for GPU", running: "Reconstructing", completed: "Complete", failed: "Failed", cancelled: "Cancelled", interrupted: "Interrupted by server restart" }[job?.state] || "Waiting";
  }

  function stateLabel(value) { return String(value || "unknown").replaceAll("_", " "); }
  function formatBytes(bytes) {
    const value = Number(bytes);
    if (!Number.isFinite(value) || value < 0) return "";
    const units = ["B", "KB", "MB", "GB", "TB"];
    let size = value;
    let unit = 0;
    while (size >= 1024 && unit < units.length - 1) { size /= 1024; unit += 1; }
    return `${size.toFixed(unit === 0 || size >= 10 ? 0 : 1)} ${units[unit]}`;
  }

  function formatDate(value) {
    if (!value) return "";
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? String(value) : date.toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
  }

  function selectedKind() { return elements.form.elements.kind.value; }
  function selectedMatcher() { return elements.form.elements.feature_matcher.value; }

  function updateMatcherNote() {
    elements.matcherNote.textContent = selectedMatcher() === "learned_matcher"
      ? "Runs feature matching on CPU on this server. Gaussian training still uses the GPU."
      : "Uses standard feature matching for this job.";
  }

  function syncCreateControls() {
    const disabled = state.online !== true || state.uploading;
    elements.form.querySelectorAll("input, select").forEach((control) => { control.disabled = disabled; });
    elements.clearFiles.disabled = disabled;
    elements.submit.disabled = disabled;
    elements.dropZone.setAttribute("aria-disabled", String(disabled));
    elements.dropZone.tabIndex = disabled ? -1 : 0;
  }

  function updateKind() {
    const isMesh = selectedKind() === "mesh";
    elements.qualityField.hidden = !isMesh;
    if (!isMesh) elements.quality.value = "medium";
  }

  function classifyFiles(incoming) {
    const allowed = new Set(["image/jpeg", "image/png"]);
    const accepted = [];
    const rejected = [];
    const names = new Set();
    for (const file of incoming) {
      const extensionOkay = /\.(jpe?g|png)$/i.test(file.name);
      if (!(allowed.has(file.type) || extensionOkay)) { rejected.push(`${file.name}: use JPG or PNG`); continue; }
      const key = file.name.toLocaleLowerCase();
      if (names.has(key)) { rejected.push(`${file.name}: duplicate filename`); continue; }
      names.add(key);
      accepted.push(file);
    }
    return { accepted, rejected };
  }

  function revokeObjectUrls() { state.objectUrls.forEach((url) => URL.revokeObjectURL(url)); state.objectUrls = []; }

  function updateFiles(files) {
    revokeObjectUrls();
    const classified = classifyFiles(files);
    state.files = classified.accepted;
    state.rejectedFiles = classified.rejected;
    elements.fileList.replaceChildren();
    elements.fileSummary.hidden = state.files.length === 0;
    elements.dropTitle.textContent = state.files.length ? "Choose a different photo set" : "Choose photos or drop them here";
    elements.fileErrors.textContent = classified.rejected.join(" · ");
    if (classified.rejected.length) announce(`${classified.rejected.length} file${classified.rejected.length === 1 ? " was" : "s were"} rejected.`);
    if (!state.files.length) return;
    const total = state.files.reduce((sum, file) => sum + file.size, 0);
    elements.fileCount.textContent = `${state.files.length} photo${state.files.length === 1 ? "" : "s"}`;
    elements.fileSize.textContent = formatBytes(total);
    state.files.slice(0, 12).forEach((file) => {
      const item = document.createElement("li");
      const image = document.createElement("img");
      const url = URL.createObjectURL(file);
      state.objectUrls.push(url);
      image.src = url;
      image.alt = "";
      const name = document.createElement("span");
      name.textContent = file.name;
      name.title = file.name;
      item.append(image, name);
      elements.fileList.append(item);
    });
    if (state.files.length > 12) {
      const item = document.createElement("li");
      const label = document.createElement("span");
      label.textContent = `${state.files.length - 12} more photos`;
      item.append(label);
      elements.fileList.append(item);
    }
    announce(`${state.files.length} photos selected.`);
  }

  function validationMessage() {
    const name = elements.name.value.trim();
    if (!name) return { message: "Give this job a name.", target: elements.name };
    if (state.files.length < 3) return { message: "Choose at least 3 unique JPG or PNG photos.", target: elements.dropZone };
    return null;
  }

  function setUploadProgress(percent, label) {
    const safe = Math.max(0, Math.min(100, percent));
    elements.uploadProgress.hidden = false;
    elements.uploadBar.style.width = `${safe}%`;
    elements.uploadPercent.textContent = `${Math.round(safe)}%`;
    elements.uploadLabel.textContent = label;
  }

  function putFile(jobId, file, onProgress) {
    return new Promise((resolve, reject) => {
      const xhr = new XMLHttpRequest();
      xhr.open("PUT", `${API}/jobs/${encodeURIComponent(jobId)}/images/${encodeURIComponent(file.name)}`);
      xhr.setRequestHeader("Content-Type", file.type || "application/octet-stream");
      xhr.setRequestHeader("Accept", "application/json");
      xhr.upload.addEventListener("progress", (event) => { if (event.lengthComputable) onProgress(event.loaded); });
      xhr.addEventListener("load", () => {
        if (xhr.status >= 200 && xhr.status < 300) resolve();
        else {
          let reason = `${xhr.status} ${xhr.statusText}`;
          try { const payload = JSON.parse(xhr.responseText); reason = payload.error || payload.message || reason; } catch (_) { /* not JSON */ }
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
    const invalid = validationMessage();
    if (invalid) {
      elements.formMessage.textContent = invalid.message;
      elements.formMessage.focus();
      invalid.target.focus();
      return;
    }
    state.uploading = true;
    syncCreateControls();
    elements.submit.textContent = "Uploading";
    const totalBytes = state.files.reduce((sum, file) => sum + file.size, 0);
    let completedBytes = 0;
    let jobId = state.resumeJobId;
    try {
      if (!jobId) {
        setUploadProgress(0, "Creating job");
        const payload = await api("/jobs", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({
            name: elements.name.value.trim(),
            kind: selectedKind(),
            quality: elements.quality.value,
            settings: { feature_matcher: selectedMatcher() },
          }),
        });
        const created = unwrapJob(payload);
        if (!created?.id) throw new Error("Server did not return a job id");
        jobId = created.id;
        state.resumeJobId = jobId;
      }
      for (let index = 0; index < state.files.length; index += 1) {
        const file = state.files[index];
        setUploadProgress(totalBytes ? (completedBytes / totalBytes) * 96 : 0, `Uploading ${index + 1} of ${state.files.length}: ${file.name}`);
        await putFile(jobId, file, (loaded) => setUploadProgress(totalBytes ? ((completedBytes + loaded) / totalBytes) * 96 : 0, `Uploading ${index + 1} of ${state.files.length}: ${file.name}`));
        completedBytes += file.size;
      }
      setUploadProgress(98, "Adding job to the queue");
      await api(`/jobs/${encodeURIComponent(jobId)}/start`, { method: "POST" });
      setUploadProgress(100, "Job queued");
      state.resumeJobId = null;
      elements.form.reset();
      elements.photoInput.value = "";
      updateFiles([]);
      updateKind();
      updateMatcherNote();
      elements.formMessage.textContent = "";
      await closeDrawer(false, "replace");
      await selectJob(jobId, "activity", true);
      showToast("Job queued.");
      announce("Job queued");
      window.setTimeout(() => { elements.uploadProgress.hidden = true; }, 1800);
      elements.detailName.focus();
    } catch (error) {
      const learnedUnavailable = selectedMatcher() === "learned_matcher" && (
        error.code === "learned_matching_unavailable" || error.code === "learned_matcher_unavailable" ||
        (error.status === 400 && /learned|feature.?matcher|unknown settings|ALIKED|LightGlue/i.test(error.message))
      );
      const diskCopy = error.code === "disk_full"
        ? "The server does not have enough free space for this job. Remove old results or choose fewer photos."
        : learnedUnavailable
          ? "This server does not include learned matching."
          : `Upload stopped: ${error.message}. Files already received remain with this job.`;
      elements.formMessage.textContent = diskCopy;
      elements.formMessage.focus();
      elements.submit.textContent = "Resume upload";
      showToast("Upload stopped. The same files can be resumed in this drawer.");
      await refreshJobs(false);
    } finally {
      state.uploading = false;
      syncCreateControls();
      if (!state.resumeJobId) elements.submit.textContent = "Create job";
    }
  }

  function chooseDefaultJob(jobs) { return jobs.find((job) => ACTIVE_STATES.has(job.state)) || jobs.find((job) => job.state === "completed") || jobs[0] || null; }

  function jobButton(job) {
    const item = document.createElement("div");
    item.setAttribute("role", "listitem");
    const button = document.createElement("button");
    button.type = "button";
    button.className = `job-row${job.id === state.selectedId ? " selected" : ""}`;
    button.dataset.jobId = job.id;
    button.setAttribute("aria-current", job.id === state.selectedId ? "true" : "false");
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
    button.addEventListener("keydown", handleQueueKeys);
    item.append(button);
    return item;
  }

  function handleQueueKeys(event) {
    if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return;
    const rows = [...elements.jobsList.querySelectorAll(".job-row")];
    let index = rows.indexOf(event.currentTarget);
    if (event.key === "ArrowDown") index = Math.min(rows.length - 1, index + 1);
    if (event.key === "ArrowUp") index = Math.max(0, index - 1);
    if (event.key === "Home") index = 0;
    if (event.key === "End") index = rows.length - 1;
    event.preventDefault();
    rows[index]?.focus();
  }

  function renderJobs() {
    elements.jobsList.replaceChildren(...state.jobs.map(jobButton));
    elements.jobsEmpty.hidden = state.jobs.length > 0;
    const active = state.jobs.filter((job) => ACTIVE_STATES.has(job.state)).length;
    elements.queueSummary.textContent = `${state.jobs.length} job${state.jobs.length === 1 ? "" : "s"}${active ? ` · ${active} active` : ""}`;
  }

  function resetJobState() {
    clearSplatViewer();
    state.logsFor = null;
    state.logCursor = 0;
    state.logText = "";
    state.artifacts = [];
    state.artifactsFor = null;
    state.artifactsFingerprint = "";
    elements.jobLogs.textContent = "Loading activity";
    elements.artifactList.replaceChildren();
    elements.filesEmpty.hidden = false;
  }

  async function selectJob(jobId, requestedView = null, replace = false) {
    if (state.selectedId !== jobId) resetJobState();
    state.selectedId = jobId;
    const summary = state.jobs.find((job) => job.id === jobId);
    state.currentView = VIEW_NAMES.has(requestedView) ? requestedView : defaultView(summary);
    updateUrl({ job: jobId, view: state.currentView }, replace ? "replace" : "push");
    document.body.classList.add("mobile-detail");
    renderJobs();
    await refreshSelectedJob();
  }

  function showNoSelection() {
    resetJobState();
    state.selectedJob = null;
    elements.detailPlaceholder.hidden = false;
    elements.jobNotFound.hidden = true;
    elements.detailContent.hidden = true;
  }

  function showNotFound() {
    resetJobState();
    state.selectedJob = null;
    elements.detailPlaceholder.hidden = true;
    elements.jobNotFound.hidden = false;
    elements.detailContent.hidden = true;
  }

  function jobMetadata(job) {
    const parts = [job.kind === "splat" ? "Gaussian splat" : "Textured mesh"];
    if (job.quality && job.kind !== "splat") parts.push(`${job.quality} quality`);
    const matcher = job.settings?.feature_matcher;
    if (matcher) parts.push(matcher === "learned_matcher" ? "Learned matching" : "Standard matching");
    const count = job.uploaded_images ?? job.photo_count ?? job.photoCount ?? job.image_count;
    if (Number.isFinite(Number(count))) parts.push(`${count} photos`);
    const created = formatDate(job.created_at || job.createdAt);
    if (created) parts.push(`created ${created}`);
    return parts.join(" · ");
  }

  function stageDefinitions(job) {
    return job.kind === "splat" ? ["Photos", "Features", "Cameras", "Train splat", "Complete"] : ["Photos", "Features", "Cameras", "Dense cloud", "Surface", "Texture", "Complete"];
  }

  function currentStageIndex(job, stages) {
    if (job.state === "completed") return stages.length - 1;
    const direct = Number(job.stage_index ?? job.stageIndex);
    if (Number.isFinite(direct)) return Math.max(0, Math.min(stages.length - 1, direct > 0 ? direct - 1 : direct));
    const label = stageText(job).toLocaleLowerCase();
    const index = stages.findIndex((name) => label.includes(name.toLocaleLowerCase().split(" ")[0]));
    if (index >= 0) return index;
    if (["uploading", "queued"].includes(job.state)) return 0;
    return Math.max(0, Math.min(stages.length - 2, Math.floor((jobProgress(job) ?? 0) * (stages.length - 1))));
  }

  function renderStages(job) {
    const stages = stageDefinitions(job);
    const current = currentStageIndex(job, stages);
    elements.stageList.replaceChildren(...stages.map((name, index) => {
      const item = document.createElement("li");
      let stepState = index < current || job.state === "completed" ? "complete" : (index === current ? "current" : "waiting");
      if (index === current && job.state === "failed") stepState = "failed";
      item.dataset.stageState = stepState;
      const label = document.createElement("span");
      label.textContent = name;
      const status = document.createElement("small");
      status.textContent = stepState === "complete" ? "Complete" : stepState === "current" ? "Current" : stepState === "failed" ? "Failed" : "Waiting";
      item.append(label, status);
      return item;
    }));
    const progress = jobProgress(job);
    elements.jobBar.style.width = `${progress === null ? 0 : progress * 100}%`;
    elements.jobPercent.textContent = progress === null ? `${current + 1} of ${stages.length} · Working` : `${current + 1} of ${stages.length} · ${Math.round(progress * 100)}%`;
  }

  function renderDiagnostic(job) {
    const diagnostics = job.capture_diagnostics;
    if (!diagnostics || job.state !== "completed") { elements.diagnostic.hidden = true; return; }
    const uploaded = Number(diagnostics.uploaded_views);
    const registered = Number(diagnostics.registered_views);
    const tracks = Number(diagnostics.reliable_tracks);
    const facts = [];
    if (Number.isFinite(uploaded) && Number.isFinite(registered)) facts.push(`${registered} of ${uploaded} views registered`);
    if (Number.isFinite(tracks)) facts.push(`${tracks.toLocaleString()} reliable tracks`);
    const level = ["good", "limited", "poor"].includes(diagnostics.level) ? diagnostics.level : "limited";
    elements.diagnostic.dataset.level = level;
    elements.diagnosticTitle.textContent = level === "poor" ? "This result has weak camera coverage" : level === "limited" ? "Limited reconstruction evidence" : "Capture evidence";
    elements.diagnosticFacts.textContent = facts.join(" · ");
    elements.diagnosticCopy.textContent = level === "good" ? "The registered views provide useful coverage for inspection." : "The viewer can orbit normally, but areas outside the registered camera arc may stretch or ghost.";
    elements.diagnosticTips.hidden = level === "good";
    elements.diagnostic.hidden = false;
  }

  function activityGuidance(job) {
    if (job.state === "interrupted") return "Server restarted. This job will return to the queue from its last completed checkpoint.";
    if (job.state === "failed") return job.error ? `Processing stopped: ${job.error}. Review the worker output below.` : "Processing stopped. Review the worker output below.";
    if (job.state === "cancelled") return "This job was cancelled. Received files remain until server cleanup.";
    if (job.state === "completed") return "Processing completed. The final worker output is retained below.";
    return "Current worker output appears below.";
  }

  function renderDetail(job) {
    const transition = state.previousJobState && (state.previousJobState !== job.state || state.previousStage !== stageText(job));
    state.selectedJob = job;
    elements.detailPlaceholder.hidden = true;
    elements.jobNotFound.hidden = true;
    elements.detailContent.hidden = false;
    elements.detailName.textContent = job.name || `Job ${job.id}`;
    elements.detailStatus.textContent = stateLabel(job.state);
    elements.detailStatus.dataset.state = job.state || "unknown";
    elements.detailMeta.textContent = jobMetadata(job);
    elements.stageLabel.textContent = stageText(job);
    if (job.error && TERMINAL_STATES.has(job.state)) elements.stageLabel.textContent = `${stageText(job)} · ${job.error}`;
    elements.activityState.textContent = stageText(job);
    elements.activityGuidance.textContent = activityGuidance(job);
    elements.cancelJob.hidden = !ACTIVE_STATES.has(job.state);
    elements.cancelJob.disabled = !state.online || (job.state === "uploading" && state.uploading);
    renderStages(job);
    renderDiagnostic(job);
    if (!state.currentView) state.currentView = defaultView(job);
    renderView();
    if (transition) announce(`${job.name || "Job"}: ${stageText(job)}.`);
    state.previousJobState = job.state;
    state.previousStage = stageText(job);
  }

  function renderView() {
    const view = VIEW_NAMES.has(state.currentView) ? state.currentView : defaultView(state.selectedJob);
    state.currentView = view;
    elements.tabs.forEach((tab) => {
      const active = tab.dataset.view === view;
      tab.setAttribute("aria-selected", String(active));
      tab.tabIndex = active ? 0 : -1;
    });
    elements.views.forEach((panel) => { panel.hidden = panel.id !== `view-${view}`; });
    if (view === "result" && state.splatViewerFor) ensureViewerLoaded();
  }

  function switchView(view, push = true) {
    if (!VIEW_NAMES.has(view) || !state.selectedId) return;
    if (state.viewerActive) releaseViewerInput();
    state.currentView = view;
    if (push) updateUrl({ view });
    renderView();
  }

  async function refreshJobs(selectDefault = false) {
    try {
      const payload = await api("/jobs");
      state.jobs = unwrapList(payload, "jobs");
      setConnection(true, "Server online");
      const url = new URL(window.location.href);
      const urlJob = url.searchParams.get("job");
      if (selectDefault || (!state.selectedId && !urlJob)) {
        const chosen = chooseDefaultJob(state.jobs);
        if (chosen) {
          state.selectedId = chosen.id;
          state.currentView = VIEW_NAMES.has(url.searchParams.get("view")) ? url.searchParams.get("view") : defaultView(chosen);
          updateUrl({ job: chosen.id, view: state.currentView }, "replace");
        }
      } else if (!state.selectedId && urlJob) {
        state.selectedId = urlJob;
        state.currentView = VIEW_NAMES.has(url.searchParams.get("view")) ? url.searchParams.get("view") : null;
      }
      renderJobs();
      if (state.selectedId && state.jobs.some((job) => job.id === state.selectedId)) await refreshSelectedJob();
      else if (state.selectedId) showNotFound();
      else {
        showNoSelection();
        if (!state.jobs.length && !elements.drawer.open) openDrawer("replace");
      }
    } catch (_) {
      setConnection(false, state.jobs.length ? "Last known queue" : "Server unavailable");
      if (!state.jobs.length) elements.jobsEmpty.hidden = false;
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
      const index = state.jobs.findIndex((entry) => entry.id === job.id);
      if (index >= 0) state.jobs[index] = { ...state.jobs[index], ...job };
      renderJobs();
      await Promise.all([refreshLogs(job), job.state === "completed" ? refreshArtifacts(job) : Promise.resolve()]);
    } catch (error) {
      if (selectedAtStart !== state.selectedId) return;
      if (error.status === 404) showNotFound();
      else elements.activityGuidance.textContent = `Unable to refresh this job: ${error.message}`;
    }
  }

  async function refreshLogs(job) {
    if (state.logsFor !== job.id) { state.logsFor = job.id; state.logCursor = 0; state.logText = ""; }
    try {
      const payload = await api(`/jobs/${encodeURIComponent(job.id)}/logs?after=${state.logCursor}`);
      let addition = "";
      let next = state.logCursor;
      if (typeof payload === "string") { addition = payload; next += addition ? addition.split("\n").length : 0; }
      else if (payload) {
        if (Array.isArray(payload.lines)) addition = payload.lines.join("\n") + (payload.lines.length ? "\n" : "");
        else addition = payload.text || "";
        next = Number(payload.next ?? payload.next_line ?? payload.cursor ?? next);
      }
      if (addition) state.logText += addition;
      if (Number.isFinite(next)) state.logCursor = next;
      const visibleLines = state.logText.split("\n").slice(-400).join("\n");
      elements.jobLogs.textContent = visibleLines || "No log output yet";
      elements.jobLogs.setAttribute("aria-live", elements.followLogs.checked ? "polite" : "off");
      if (elements.followLogs.checked) elements.jobLogs.scrollTop = elements.jobLogs.scrollHeight;
    } catch (_) { /* retain the last available log */ }
  }

  function artifactUrl(jobId, artifact) {
    if (artifact.url) return artifact.url;
    return `${API}/jobs/${encodeURIComponent(jobId)}/artifacts/${encodeURIComponent(artifact.name || artifact.id)}`;
  }

  function splatArtifact(artifacts) {
    let latest = null;
    let rank = -1;
    artifacts.forEach((artifact, index) => {
      const name = artifact.name || artifact.id || "";
      if (!/\.ply$/i.test(name)) return;
      const checkpoint = name.match(/(\d+)(?=\.ply$)/i);
      const candidateRank = checkpoint ? Number(checkpoint[1]) : index;
      if (!latest || candidateRank >= rank) { latest = artifact; rank = candidateRank; }
    });
    return latest;
  }

  function settingsArtifact(artifacts) { return artifacts.find((artifact) => /(^|\/)viewer-settings\.json$/i.test(artifact.name || artifact.id || "")); }

  function clearViewerLoadTimers() {
    window.clearTimeout(state.viewerSlowTimer);
    window.clearTimeout(state.viewerTimeoutTimer);
    state.viewerSlowTimer = null;
    state.viewerTimeoutTimer = null;
  }

  function beginViewerLoad(label) {
    clearViewerLoadTimers();
    state.viewerLoadToken += 1;
    state.viewerReady = false;
    state.viewerLoadFailed = false;
    const token = state.viewerLoadToken;
    elements.viewerStatus.textContent = label;
    state.viewerSlowTimer = window.setTimeout(() => {
      if (token !== state.viewerLoadToken || state.viewerReady) return;
      elements.viewerStatus.textContent = state.viewerResetting ? "Still resetting camera" : "Still loading interactive result";
    }, CONFIG.viewerSlowMs);
    state.viewerTimeoutTimer = window.setTimeout(() => {
      failViewerLoad(token, "The viewer timed out before the first frame. Select Reset camera to retry or download the splat.");
    }, CONFIG.viewerTimeoutMs);
    return token;
  }

  function failViewerLoad(token, message) {
    if (token !== state.viewerLoadToken || state.viewerReady) return;
    clearViewerLoadTimers();
    state.viewerLoadFailed = true;
    state.viewerResetting = false;
    state.viewerResumeAfterReset = false;
    releaseViewerInput();
    elements.resetCamera.disabled = false;
    elements.activateViewer.disabled = true;
    elements.viewerStatus.textContent = message;
    announce(message);
  }

  function completeViewerFirstFrame(token) {
    if (token !== state.viewerLoadToken || state.viewerReady) return;
    const wasResetting = state.viewerResetting;
    clearViewerLoadTimers();
    state.viewerReady = true;
    state.viewerLoadFailed = false;
    state.viewerResetting = false;
    const resumeInteraction = wasResetting && state.viewerResumeAfterReset;
    state.viewerResumeAfterReset = false;
    setViewerControlsDisabled(false);
    if (resumeInteraction) {
      activateViewer({ announceResult: false });
      elements.viewerStatus.textContent = "Camera reset · Interactive viewer active";
      announce("Camera reset. Interactive viewer active.");
    } else {
      releaseViewerInput();
      elements.viewerStatus.textContent = wasResetting ? "Camera reset · Select Explore in 3D" : "Interactive result ready · Select Explore in 3D";
      if (wasResetting) {
        elements.resetCamera.focus();
        announce("Camera reset to the generated starting angle.");
      }
    }
  }

  function viewerIsFullscreen() {
    const parentFullscreen = document.fullscreenElement;
    let childFullscreen = null;
    try { childFullscreen = state.viewerDocument?.fullscreenElement; } catch (_) { /* no same-origin document */ }
    return parentFullscreen === elements.splatViewer || parentFullscreen === elements.splatViewerFrame || Boolean(childFullscreen);
  }

  function syncViewerFullscreenState() {
    const viewerFullscreen = viewerIsFullscreen();
    const exitedViewerFullscreen = state.viewerFullscreen && !viewerFullscreen;
    state.viewerFullscreen = viewerFullscreen;
    elements.fullscreenViewer.textContent = viewerFullscreen ? "Exit full screen" : "Full screen";
    if (exitedViewerFullscreen && state.viewerActive) releaseViewerInput({ focus: true, announceResult: true });
  }

  function detachViewerBridge() {
    if (!state.viewerHandlers) return;
    const { document: viewerDocument, window: viewerWindow, wheel, keydown, pointerdown, pointerup, fullscreenchange, error, unhandledrejection, firstFrame } = state.viewerHandlers;
    viewerDocument?.removeEventListener("wheel", wheel);
    viewerDocument?.removeEventListener("keydown", keydown);
    viewerDocument?.removeEventListener("pointerdown", pointerdown);
    viewerDocument?.removeEventListener("pointerup", pointerup);
    viewerDocument?.removeEventListener("fullscreenchange", fullscreenchange);
    viewerWindow?.removeEventListener("error", error);
    viewerWindow?.removeEventListener("unhandledrejection", unhandledrejection);
    try { if (viewerWindow?.firstFrame === firstFrame) delete viewerWindow.firstFrame; } catch (_) { /* document was replaced */ }
    state.viewerDocument = null;
    state.viewerHandlers = null;
  }

  function clearSplatViewer() {
    releaseViewerInput();
    detachViewerBridge();
    clearViewerLoadTimers();
    state.viewerLoadToken += 1;
    state.splatViewerFor = null;
    state.viewerUrl = null;
    state.viewerReady = false;
    state.viewerResetting = false;
    state.viewerLoadFailed = false;
    state.viewerResumeAfterReset = false;
    setViewerControlsDisabled(true);
    elements.splatViewer.hidden = true;
    elements.splatViewerFrame.removeAttribute("src");
    elements.downloadSplat.removeAttribute("href");
  }

  function ensureViewerLoaded() {
    if (!state.viewerUrl || elements.splatViewerFrame.getAttribute("src")) return;
    setViewerControlsDisabled(true);
    beginViewerLoad("Loading interactive result");
    elements.splatViewerFrame.src = state.viewerUrl;
  }

  function renderSplatViewer(job, artifacts) {
    const artifact = job.kind === "splat" ? splatArtifact(artifacts) : null;
    if (!artifact) { clearSplatViewer(); return false; }
    const artifactName = artifact.name || artifact.id;
    const sourceUrl = artifactUrl(job.id, artifact);
    const settings = settingsArtifact(artifacts);
    const params = new URLSearchParams({ content: sourceUrl });
    if (settings) {
      const settingsUrl = artifactUrl(job.id, settings);
      const settingsVersion = settings.size ?? settings.bytes;
      params.set("settings", settingsVersion == null ? settingsUrl : `${settingsUrl}${settingsUrl.includes("?") ? "&" : "?"}v=${encodeURIComponent(settingsVersion)}`);
    }
    params.set("nofx", "");
    params.set("noanim", "");
    const viewerUrl = `/viewer/index.html?${params}`;
    const viewerKey = `${job.id}:${artifactName}:${artifact.size ?? artifact.bytes ?? ""}:${settings?.name || settings?.id || "fallback"}:${settings?.size ?? settings?.bytes ?? ""}`;
    elements.splatViewerLabel.textContent = artifactName;
    elements.viewerFileMeta.textContent = ["Gaussian splat", formatBytes(artifact.size ?? artifact.bytes), settings ? "Core-framed" : "Automatic framing"].filter(Boolean).join(" · ");
    elements.splatViewerFrame.title = `Interactive Gaussian splat result for ${job.name || job.id}`;
    elements.downloadSplat.href = sourceUrl;
    elements.downloadSplat.download = artifactName;
    elements.splatViewer.hidden = false;
    if (state.splatViewerFor !== viewerKey) {
      releaseViewerInput();
      detachViewerBridge();
      elements.splatViewerFrame.removeAttribute("src");
      state.splatViewerFor = viewerKey;
      state.viewerUrl = viewerUrl;
      state.viewerReady = false;
      state.viewerResetting = false;
      state.viewerLoadFailed = false;
      state.viewerResumeAfterReset = false;
      setViewerControlsDisabled(true);
      elements.viewerActivation.hidden = false;
      const bytes = Number(artifact.size ?? artifact.bytes);
      elements.viewerStatus.textContent = Number.isFinite(bytes) && bytes > CONFIG.viewerMemoryWarningBytes ? `Large result: ${formatBytes(bytes)}. Close other tabs before exploring.` : "Loading interactive result";
      if (state.currentView === "result") ensureViewerLoaded();
    }
    return true;
  }

  function installViewerBridge() {
    detachViewerBridge();
    try {
      const doc = elements.splatViewerFrame.contentDocument;
      const viewerWindow = elements.splatViewerFrame.contentWindow;
      if (!doc || !viewerWindow) return;
      const token = state.viewerLoadToken;
      const wheel = (event) => { if (state.viewerActive) event.preventDefault(); };
      const keydown = (event) => { if (event.key === "Escape" && state.viewerActive) { event.preventDefault(); releaseViewerInput({ focus: true, announceResult: true }); } };
      const pointerdown = () => { if (state.viewerActive) doc.querySelector("canvas")?.style.setProperty("cursor", "grabbing"); };
      const pointerup = () => { if (state.viewerActive) doc.querySelector("canvas")?.style.setProperty("cursor", "grab"); };
      const fullscreenchange = () => syncViewerFullscreenState();
      const error = (event) => {
        const errorDetail = event.message ? `: ${event.message}.` : ".";
        failViewerLoad(token, `Interactive view failed to render${errorDetail} Download the splat or select Reset camera to retry.`);
      };
      const unhandledrejection = () => failViewerLoad(token, "Interactive view failed to render. Download the splat or select Reset camera to retry.");
      const firstFrame = () => completeViewerFirstFrame(token);
      doc.addEventListener("wheel", wheel, { passive: false });
      doc.addEventListener("keydown", keydown);
      doc.addEventListener("pointerdown", pointerdown);
      doc.addEventListener("pointerup", pointerup);
      doc.addEventListener("fullscreenchange", fullscreenchange);
      viewerWindow.addEventListener("error", error);
      viewerWindow.addEventListener("unhandledrejection", unhandledrejection);
      viewerWindow.firstFrame = firstFrame;
      state.viewerDocument = doc;
      state.viewerHandlers = { document: doc, window: viewerWindow, wheel, keydown, pointerdown, pointerup, fullscreenchange, error, unhandledrejection, firstFrame };
      const canvas = doc.querySelector("canvas");
      if (canvas) { canvas.tabIndex = 0; canvas.style.cursor = state.viewerActive ? "grab" : "default"; }
      const fastLoadRecheck = () => { if (viewerWindow.app) firstFrame(); };
      queueMicrotask(fastLoadRecheck);
      viewerWindow.requestAnimationFrame(fastLoadRecheck);
    } catch (_) {
      failViewerLoad(state.viewerLoadToken, "Interactive view could not be controlled in this browser. Download the splat or select Reset camera to retry.");
    }
  }

  function setViewerControlsDisabled(disabled) {
    elements.resetCamera.disabled = disabled;
    elements.activateViewer.disabled = disabled;
  }

  function releaseViewerInput({ focus = false, announceResult = false } = {}) {
    state.viewerActive = false;
    elements.viewerFrameShell.classList.remove("viewer-active");
    elements.viewerActivation.hidden = false;
    elements.exitViewer.hidden = true;
    elements.splatViewerFrame.tabIndex = -1;
    try { state.viewerDocument?.querySelector("canvas")?.style.setProperty("cursor", "default"); } catch (_) { /* no bridge */ }
    if (state.viewerReady && !state.viewerResetting) elements.viewerStatus.textContent = "Interactive result ready · Select Explore in 3D";
    if (focus) elements.activateViewer.focus();
    if (announceResult) announce("Viewer input released. Page scrolling available; camera position preserved.");
  }

  function activateViewer({ announceResult = true } = {}) {
    if (!state.viewerUrl || !state.viewerReady || state.viewerResetting) return;
    elements.splatViewerFrame.tabIndex = 0;
    state.viewerActive = true;
    elements.viewerFrameShell.classList.add("viewer-active");
    elements.viewerActivation.hidden = true;
    elements.exitViewer.hidden = false;
    elements.viewerStatus.textContent = "Activating interactive camera";
    localStorage.setItem("viewerHelpSeen", "1");
    try {
      const canvas = elements.splatViewerFrame.contentDocument?.querySelector("canvas");
      if (canvas) { canvas.tabIndex = 0; canvas.style.cursor = "grab"; canvas.focus(); }
      else elements.splatViewerFrame.focus();
    } catch (_) { elements.splatViewerFrame.focus(); }
    elements.viewerStatus.textContent = "Interactive viewer active · Press Escape to return to page scrolling";
    if (announceResult) announce("Interactive viewer active. Press Escape to return to page scrolling.");
  }

  function resetViewerCamera() {
    if (!state.viewerUrl || state.viewerResetting) return;
    state.viewerResumeAfterReset = state.viewerActive;
    releaseViewerInput();
    state.viewerResetting = true;
    setViewerControlsDisabled(true);
    beginViewerLoad("Resetting camera");
    try { elements.splatViewerFrame.contentWindow.location.reload(); }
    catch (_) { elements.splatViewerFrame.src = state.viewerUrl; }
  }

  async function toggleFullscreen() {
    try {
      if (document.fullscreenElement === elements.splatViewer) await document.exitFullscreen();
      else await elements.splatViewer.requestFullscreen();
    } catch (_) {
      elements.viewerStatus.textContent = "Full screen is unavailable in this browser";
    }
  }

  function renderArtifacts(job, artifacts) {
    const normalized = artifacts.map((artifact) => typeof artifact === "string" ? { name: artifact } : artifact);
    elements.artifactList.replaceChildren(...normalized.map((artifact) => {
      const row = document.createElement("tr");
      const name = document.createElement("td");
      name.className = "artifact-name";
      name.dataset.label = "Name";
      name.textContent = artifact.label || artifact.name || artifact.id || "Result file";
      const type = document.createElement("td");
      type.dataset.label = "Type";
      type.textContent = artifact.kind || artifact.mime || "file";
      const size = document.createElement("td");
      size.dataset.label = "Size";
      size.textContent = formatBytes(artifact.size ?? artifact.bytes) || "Unknown";
      const action = document.createElement("td");
      action.dataset.label = "Download";
      const link = document.createElement("a");
      link.href = artifactUrl(job.id, artifact);
      link.download = artifact.name || "";
      link.textContent = "Download";
      action.append(link);
      row.append(name, type, size, action);
      return row;
    }));
    elements.filesEmpty.hidden = normalized.length > 0;
    const image = normalized.find((artifact) => artifact.kind === "thumbnail" || String(artifact.mime || "").startsWith("image/") || /\.(png|jpe?g|webp)$/i.test(artifact.name || ""));
    const hasViewer = renderSplatViewer(job, normalized);
    if (image && !hasViewer) {
      elements.previewImage.src = artifactUrl(job.id, image);
      elements.preview.hidden = false;
    } else {
      elements.preview.hidden = true;
      elements.previewImage.removeAttribute("src");
    }
    elements.previewFallback.hidden = hasViewer || Boolean(image);
    elements.resultMetadata.hidden = normalized.length === 0;
    elements.resultMetadata.textContent = `${normalized.length} result file${normalized.length === 1 ? "" : "s"} · stored on servOS`;
  }

  async function refreshArtifacts(job) {
    try {
      const payload = await api(`/jobs/${encodeURIComponent(job.id)}/artifacts`);
      if (job.id !== state.selectedId) return;
      const artifacts = unwrapList(payload, "artifacts");
      const fingerprint = JSON.stringify(artifacts.map((item) => typeof item === "string" ? item : [item.name, item.id, item.size, item.bytes, item.url]));
      state.artifacts = artifacts;
      state.artifactsFor = job.id;
      if (fingerprint !== state.artifactsFingerprint) { state.artifactsFingerprint = fingerprint; renderArtifacts(job, artifacts); }
    } catch (_) {
      if (job.id === state.selectedId && !state.artifacts.length) {
        elements.filesEmpty.textContent = "Result files could not be refreshed. Reconnect to the server and try again.";
        elements.filesEmpty.hidden = false;
        clearSplatViewer();
      }
    }
  }

  async function cancelSelectedJob() {
    const job = state.selectedJob;
    if (!job || !ACTIVE_STATES.has(job.state)) return;
    if (!window.confirm(`Cancel “${job.name || job.id}”? Received files will remain until server cleanup.`)) return;
    elements.cancelJob.disabled = true;
    try { await api(`/jobs/${encodeURIComponent(job.id)}/cancel`, { method: "POST" }); showToast("Cancellation requested."); await refreshJobs(false); }
    catch (error) { showToast(`Could not cancel: ${error.message}`); }
    finally { elements.cancelJob.disabled = !state.online; }
  }

  async function copyLogs() {
    try { await navigator.clipboard.writeText(state.logText); showToast("Log copied."); }
    catch (_) { showToast("Clipboard access is unavailable in this browser."); }
  }

  function openDrawer(mode = "push") {
    if (!elements.drawer.open) elements.drawer.showModal();
    elements.newReconstruction.hidden = true;
    updateUrl({ new: 1 }, mode);
    window.setTimeout(() => elements.name.focus(), 0);
  }

  async function closeDrawer(confirmUpload = true, mode = "replace") {
    if (state.uploading && confirmUpload && !window.confirm("Leave this upload? Files already received will stay with the job.")) return false;
    if (elements.drawer.open) elements.drawer.close();
    elements.newReconstruction.hidden = false;
    updateUrl({ new: null }, mode);
    elements.newReconstruction.focus();
    return true;
  }

  function toggleCaptureTips(open = elements.captureTips.hidden) {
    elements.captureTips.hidden = !open;
    elements.diagnosticTips.setAttribute("aria-expanded", String(open));
    if (open) elements.captureTips.scrollIntoView({ block: "nearest" });
  }

  function handlePopState() {
    const url = new URL(window.location.href);
    const jobId = url.searchParams.get("job");
    const view = url.searchParams.get("view");
    const drawerRequested = url.searchParams.get("new") === "1";
    if (drawerRequested && !elements.drawer.open) { elements.drawer.showModal(); elements.newReconstruction.hidden = true; }
    if (!drawerRequested && elements.drawer.open) { elements.drawer.close(); elements.newReconstruction.hidden = false; }
    if (!jobId) {
      state.selectedId = null;
      state.currentView = null;
      document.body.classList.remove("mobile-detail");
      showNoSelection();
      return;
    }
    if (jobId !== state.selectedId) resetJobState();
    state.selectedId = jobId;
    state.currentView = VIEW_NAMES.has(view) ? view : null;
    document.body.classList.add("mobile-detail");
    refreshSelectedJob();
  }

  function bindEvents() {
    elements.form.addEventListener("submit", submitJob);
    elements.form.addEventListener("change", (event) => {
      if (event.target.name === "kind") updateKind();
      if (event.target.name === "feature_matcher") updateMatcherNote();
    });
    elements.dropZone.addEventListener("click", () => { if (elements.dropZone.getAttribute("aria-disabled") !== "true") elements.photoInput.click(); });
    elements.dropZone.addEventListener("keydown", (event) => {
      if ((event.key === "Enter" || event.key === " ") && elements.dropZone.getAttribute("aria-disabled") !== "true") { event.preventDefault(); elements.photoInput.click(); }
    });
    elements.photoInput.addEventListener("change", () => updateFiles([...elements.photoInput.files]));
    elements.clearFiles.addEventListener("click", () => { elements.photoInput.value = ""; updateFiles([]); });
    ["dragenter", "dragover"].forEach((name) => elements.dropZone.addEventListener(name, (event) => { event.preventDefault(); elements.dropZone.classList.add("dragging"); }));
    ["dragleave", "drop"].forEach((name) => elements.dropZone.addEventListener(name, (event) => { event.preventDefault(); elements.dropZone.classList.remove("dragging"); }));
    elements.dropZone.addEventListener("drop", (event) => updateFiles([...event.dataTransfer.files]));
    elements.newReconstruction.addEventListener("click", () => openDrawer());
    elements.emptyNew.addEventListener("click", () => openDrawer());
    elements.closeDrawer.addEventListener("click", () => closeDrawer());
    elements.cancelCreate.addEventListener("click", () => closeDrawer());
    elements.drawer.addEventListener("cancel", (event) => { event.preventDefault(); closeDrawer(); });
    elements.refreshJobs.addEventListener("click", () => refreshJobs(false));
    elements.backToJobs.addEventListener("click", () => { updateUrl({ job: null, view: null }); document.body.classList.remove("mobile-detail"); showNoSelection(); });
    elements.tabs.forEach((tab) => {
      tab.addEventListener("click", () => switchView(tab.dataset.view));
      tab.addEventListener("keydown", (event) => {
        if (!["ArrowRight", "ArrowLeft", "Home", "End"].includes(event.key)) return;
        let index = elements.tabs.indexOf(event.currentTarget);
        if (event.key === "ArrowRight") index = (index + 1) % elements.tabs.length;
        if (event.key === "ArrowLeft") index = (index - 1 + elements.tabs.length) % elements.tabs.length;
        if (event.key === "Home") index = 0;
        if (event.key === "End") index = elements.tabs.length - 1;
        event.preventDefault();
        elements.tabs[index].focus();
        switchView(elements.tabs[index].dataset.view);
      });
    });
    document.querySelectorAll("[data-open-view]").forEach((button) => button.addEventListener("click", () => switchView(button.dataset.openView)));
    elements.diagnosticTips.addEventListener("click", () => toggleCaptureTips());
    elements.closeCaptureTips.addEventListener("click", () => toggleCaptureTips(false));
    elements.cancelJob.addEventListener("click", cancelSelectedJob);
    elements.copyLogs.addEventListener("click", copyLogs);
    elements.followLogs.addEventListener("change", () => {
      localStorage.setItem("followLogs", String(elements.followLogs.checked));
      elements.jobLogs.setAttribute("aria-live", elements.followLogs.checked ? "polite" : "off");
    });
    elements.activateViewer.addEventListener("click", activateViewer);
    elements.exitViewer.addEventListener("click", () => releaseViewerInput({ focus: true, announceResult: true }));
    elements.resetCamera.addEventListener("click", resetViewerCamera);
    elements.viewerControls.addEventListener("click", () => {
      const open = elements.controlsHelp.hidden;
      elements.controlsHelp.hidden = !open;
      elements.viewerControls.setAttribute("aria-expanded", String(open));
    });
    elements.fullscreenViewer.addEventListener("click", toggleFullscreen);
    elements.splatViewerFrame.addEventListener("load", () => {
      if (!state.viewerUrl || elements.splatViewerFrame.src === "about:blank") return;
      installViewerBridge();
    });
    document.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && state.viewerActive) { event.preventDefault(); releaseViewerInput({ focus: true, announceResult: true }); return; }
      if (event.key === "?" && !["INPUT", "SELECT", "TEXTAREA"].includes(event.target.tagName)) {
        event.preventDefault();
        elements.controlsHelp.hidden = !elements.controlsHelp.hidden;
        elements.viewerControls.setAttribute("aria-expanded", String(!elements.controlsHelp.hidden));
      }
    });
    document.addEventListener("fullscreenchange", syncViewerFullscreenState);
    elements.dismissToast.addEventListener("click", () => { elements.toast.hidden = true; });
    window.addEventListener("popstate", handlePopState);
    document.addEventListener("visibilitychange", () => { if (!document.hidden) refreshJobs(false); });
    window.addEventListener("beforeunload", (event) => { if (state.uploading) { event.preventDefault(); event.returnValue = ""; } });
  }

  async function poll() {
    if (state.polling || document.hidden) return;
    state.polling = true;
    try { await refreshJobs(false); } finally { state.polling = false; }
  }

  function initializeUrlState() {
    const url = new URL(window.location.href);
    state.initialUrlHadJob = url.searchParams.has("job");
    state.selectedId = url.searchParams.get("job");
    const view = url.searchParams.get("view");
    state.currentView = VIEW_NAMES.has(view) ? view : null;
    if (state.selectedId) document.body.classList.add("mobile-detail");
    if (url.searchParams.get("new") === "1") { elements.drawer.showModal(); elements.newReconstruction.hidden = true; }
  }

  bindEvents();
  updateKind();
  updateMatcherNote();
  syncCreateControls();
  elements.followLogs.checked = localStorage.getItem("followLogs") !== "false";
  initializeUrlState();
  poll();
  window.setInterval(poll, POLL_MS);
})();
