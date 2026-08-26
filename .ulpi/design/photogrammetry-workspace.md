---
feature: photogrammetry workspace and result viewer
status: locked for implementation
binds_to: .ulpi/design/DESIGN.md
target_agent: react-vite-tailwind-engineer
design_system: Primer CSS + Octicons
delivery: offline static SPA served by the existing Python service
---

# Photogrammetry Workspace

Every screen must read as the same product if placed side by side.

Read `.ulpi/design/DESIGN.md` before implementation. Every color, type role, gap, radius, icon, motion, and elevation comes from that file. No production value may be invented in this spec or during implementation.

## Product Intent

### Goal

Let one home-server operator create, monitor, diagnose, inspect, and download photogrammetry mesh or Gaussian splat jobs without needing Cockpit or terminal access for ordinary work.

### Audience and context

- Primary user: the servOS owner, technically capable but not expected to know COLMAP or Brush internals.
- Primary environment: desktop browser over LAN or Tailscale, often left open while a GPU job runs.
- Secondary environment: phone browser for checking progress, reviewing diagnostics, cancelling a bad run, and downloading small artifacts.
- Security perimeter: LAN and tailnet. Application authentication is out of scope for this version; a future auth layer must preserve the same flows.
- Accessibility: WCAG 2.2 AA, complete keyboard use outside the inherently visual canvas, visible focus, labeled status, and 48px mobile targets.

### Current issues this pass must resolve

1. The interface reads as a collection of large dark cards rather than one reconstruction workspace.
2. The splat canvas accepts orbit input, but movement is not discoverable before interaction.
3. Wheel input may scroll the page because viewer focus and scroll ownership are unclear.
4. A poor reconstruction can look like a broken or fixed viewer. The current sample registered only 7 views with 31 reliable tracks, and its transparent or reflective acrylic plus thin fibers produce stretched, ghosted geometry outside the learned camera arc.
5. Capture quality, processing state, viewer state, and download state need distinct language. Do not collapse them into one generic error.

## Information Architecture

### Desktop, `lg` and above

1. **Utility header**: brand/home, server reachability, one primary `New reconstruction` action.
2. **Queue rail**: 320px to 360px ruled list of jobs. It is persistent, scrolls independently, and never becomes a card grid.
3. **Job workspace**: selected job title, reconstruction rail, capture diagnostic, and one content view.
4. **Content views**: `Result`, `Activity`, and `Files`. Result is default for completed jobs; Activity is default for queued, running, failed, cancelled, or interrupted jobs.
5. **New reconstruction drawer**: opens from the right over the job workspace. The queue remains visible as spatial context.

The selected result is the focal point. Header and queue stay visually quiet. Completed Gaussian jobs devote at least 60% of the visible job workspace height to the Calibrated Viewport.

### Tablet, `md` to below `lg`

- Queue becomes a 240px left rail when width permits; otherwise it becomes a horizontal ruled list above the selected job.
- New reconstruction is a right drawer at 80vw, capped at 560px.
- Result, Activity, and Files remain tabs. Do not stack all three into a long dashboard.

### Mobile, below `md`

- Queue is the first view. Selecting a job pushes a full-width job view and writes `job` to the URL.
- A labeled `Back to jobs` action precedes the job title.
- New reconstruction is a full-screen sheet with safe-area padding.
- Viewer height is `56dvh`, with a minimum of 360px and a maximum of 560px. The active viewer displays a persistent `Exit viewer` control so the page never becomes a scroll trap.
- Tabs stay horizontally scrollable if translations overflow. They never shrink below a 48px target.

### Layout families

The page intentionally uses five layout families:

1. minimal utility header;
2. ruled queue list;
3. linear reconstruction rail;
4. edge-to-edge calibrated viewport;
5. table-first artifacts and terminal-style activity log.

Do not turn these into repeated cards or place cards inside cards.

## URL and State Contract

Use URL state so refresh and back navigation are predictable:

```text
/?job=<uuid>&view=result|activity|files&new=1
```

- `job`: selected job. If absent, choose the first active job, then the newest completed job. If no jobs exist, show the empty queue and open the new reconstruction flow.
- `view`: selected job content view. Default derives from job state as described above.
- `new=1`: new reconstruction drawer open. Closing it removes only this parameter.
- Browser Back closes the drawer before leaving the selected job. A second Back returns from mobile job detail to queue.
- Invalid or missing job IDs show `Job not found` in the detail region and retain the queue.
- Do not store authoritative job state in `localStorage`. The server remains the source of truth.
- Local storage may hold only `viewerHelpSeen`, the last log follow preference, and non-sensitive display preferences.

## Flow 1: Create and Upload a Job

**Goal:** submit a valid photo set and place it in the server queue.

**Entry points:** empty queue action, utility-header action, `?new=1`.

```text
[Open drawer]
    -> [Name and output type]
    -> [Select photos]
    -> [Review capture guidance]
    -> [Upload files]
    -> [Queue job]
    -> [Open Activity view]
```

### Drawer structure

Keep each decision group to four or fewer controls:

1. **Job**: name; output choice `Textured mesh` or `Gaussian splat`.
2. **Quality**: mesh-only `Low`, `Medium`, `High`; hide rather than disable for splats.
3. **Photos**: drop/select field, file count, total bytes, compact contact sheet, validation.
4. **Capture guide**: collapsed by default, opened with a secondary action. It lists overlap, lighting, material, and coverage guidance before upload.

The only primary drawer action is `Create job`. `Cancel` and `Clear photos` are secondary.

### Validation

- Name is required, trimmed, 1 to 80 Unicode characters.
- Accept JPG, JPEG, and PNG only.
- Require at least three unique filenames. Recommend 30 or more around a small object without blocking submission.
- Duplicate names are listed before upload and ignored until renamed or removed.
- Rejected files remain visible in a separate validation line with cause.
- Validation errors sit beside their field, receive focus on submission, and are summarized in the drawer's assertive live region.

### Upload behavior

- Create the server job, then upload files in sequence using the existing idempotent filename endpoint.
- Show aggregate bytes, current filename, `n of total`, and percentage. The progress bar is solid `accent`, not a gradient.
- While uploading, closing the drawer requires confirmation. Copy: `Leave this upload? Files already received will stay with the job.`
- On connection loss, keep selected `File` objects in memory and offer `Resume upload`. The backend replaces matching filenames idempotently.
- After browser refresh, files cannot be restored for security reasons. The uploading job remains in the queue with `Reselect photos to resume` and reports filenames or counts already received.
- On disk-full response, say how much free space is required if the API provides it; otherwise: `The server does not have enough free space for this job. Remove old results or choose fewer photos.`
- On success, close the drawer, select the new job, open Activity, announce `Job queued`, and move focus to the selected-job heading.

## Flow 2: Monitor and Recover a Job

**Goal:** understand what the server is doing and recover from expected interruptions.

```text
[Select job]
    -> [Read state and stage]
    -> [Watch rail and activity]
    -> {completed | failed | cancelled | interrupted}
       completed -> [Result]
       failed -> [Cause and retry guidance]
       cancelled -> [Partial-file status]
       interrupted -> [Recovery state, then queued]
```

### Reconstruction rail

- Mesh stages: `Photos`, `Features`, `Cameras`, `Dense cloud`, `Surface`, `Texture`, `Complete`.
- Splat stages: `Photos`, `Features`, `Cameras`, `Train splat`, `Complete`.
- Completed stages use `success`, current stage uses `accent`, waiting stages use `text-subtle`, and a failed current stage uses `danger`.
- The rail always includes a text stage label and numeric `current of total`; color is never the only signal.
- Progress may be indeterminate only when the backend has no stage total. Use a stationary striped-free track with label `Working`; no infinite shimmer.

### Cancel and retry

- `Cancel job` is a danger secondary action in Activity only while uploading, queued, or running.
- Confirmation names the job and states that received files remain until cleanup.
- Failed and cancelled jobs expose `Retry job` only when the API supports requeue from the current checkpoint. Do not present a dead action.
- Interrupted jobs say `Server restarted. This job will return to the queue from its last completed checkpoint.`

## Flow 3: Inspect a Gaussian Splat

**Goal:** make camera movement obvious, separate rendering controls from data quality, and preserve page navigation.

### Entry

When a completed splat is selected, show in order:

1. job title and status;
2. reconstruction rail;
3. capture-quality diagnostic, if warning or danger;
4. Calibrated Viewport;
5. short result metadata line and download action.

### Viewer activation and scroll ownership

- Before activation, the viewport displays the rendered poster or first frame behind a light activation scrim. Wheel and trackpad gestures scroll the page.
- The scrim has one explicit action: `Explore in 3D`. It also shows `Drag to orbit · right-drag to pan · wheel or pinch to zoom` in utility type.
- Activating removes the scrim, focuses the same-origin viewer canvas, draws the `accent` focus boundary, and announces `3D viewer active. Press Escape to return to page scrolling.`
- While active, wheel and pinch belong to the viewer and must not scroll the parent page. Attach a non-passive wheel handler inside the same-origin iframe only during this state.
- `Escape` deactivates the viewer, restores page scroll, and returns focus to `Explore in 3D` or the viewer toolbar.
- On touch, activation is still explicit. A persistent 48px `Exit viewer` action sits at the top edge while active.
- Pointer cursor changes to `grab` over an active orbit canvas and `grabbing` during drag.

### Camera and help controls

The toolbar is outside the iframe and remains visible:

- `Frame result`: resets the camera to the job's generated dense-core pose. This is also the reset action and must be described to assistive technology as `Reset camera and frame the entire result`.
- `Controls`: opens a non-modal help popover with mouse, trackpad, touch, and keyboard guidance; `?` opens it when focus is in application chrome.
- `Full screen`: opens the same viewer and artifact in a dedicated same-origin page. Full screen repeats the help and Exit behavior.
- `Download splat`: direct artifact download, visually subordinate to the viewer action.

Do not add fake `Orbit` or `Pan` mode buttons unless the pinned viewer exposes a tested public command for them. The viewer already supports free camera interaction; this pass exposes it rather than simulating controls.

### Camera framing contract

- The worker emits a per-job `viewer-settings.json` next to a splat artifact.
- Prefer the 5th through 95th percentile bounds of triangulated COLMAP sparse tracks as the subject focus, because they represent geometry actually shared across registered views. Frame that AABB at 55 degrees FOV with 35% padding. When sparse tracks are unavailable, fall back to the 1st through 99th splat-position percentiles with 12% padding. Record the method and trim fraction in the settings artifact. This excludes distant Gaussian floaters and avoids centering a neutral background instead of the matched subject.
- If bounds cannot be read, use the viewer's object framing default and record `framing: automatic` in result metadata.
- `Frame result` reloads the camera pose without refetching the PLY when the viewer API permits. If the pinned static viewer does not expose this command, reload the viewer document with the same content and settings URLs. Preserve a visible `Resetting view` state during reload.
- Never hard-code `[0, 1, -3]` for every result.

### Viewer states

| state | visible treatment | recovery |
|---|---|---|
| Poster | first frame, activation scrim, control summary | select `Explore in 3D` |
| Loading | native load percentage plus filename and bytes | wait; after 10 seconds add `Still loading` without declaring failure |
| Ready inactive | first rendered frame, scrim restored after Escape | activate or download |
| Active | focus boundary, camera controls, `Exit viewer` | Escape or Exit |
| Resetting | retained poster and `Resetting view` status | returns to inactive ready |
| Unsupported GPU/browser | `Interactive view is unavailable in this browser` | download PLY; link to browser requirements |
| Artifact missing | `The splat file is not available` | refresh artifacts; show activity log |
| Decode/render failure | cause if known, no false capture-quality diagnosis | retry viewer; download original |
| Low-quality reconstruction | viewer remains available with warning diagnostic above it | open capture tips or download |
| Offline after load | retain current frame if possible; label server offline | continue local interaction; downloads disabled |

The canvas is visual content. Give the iframe a specific title containing the job name and provide text metadata, diagnostics, and direct download as the non-visual alternative.

## Capture-Quality Diagnostic

This is not a viewer error and must never be placed inside the canvas. It is a ruled diagnostic strip between the reconstruction rail and viewport.

### Data contract

Add this optional object to completed job detail:

```json
{
  "capture_diagnostics": {
    "uploaded_views": 42,
    "registered_views": 38,
    "reliable_tracks": 4200,
    "level": "good | limited | poor",
    "notes": ["low_view_registration", "low_track_count"]
  }
}
```

Backend classification is deterministic:

- registration ratio below 70% is `poor`; 70% through 89% is `limited`; 90% or more is `good`;
- fewer than 500 reliable tracks is `poor`; 500 through 1,999 is `limited`; 2,000 or more is `good`;
- overall level is the worse of registration and tracks;
- material risk is not guessed from images in v1. Material tips appear only as capture guidance, not as an automated claim.

### Presentation

- `good`: one compact metadata line, not a success banner.
- `limited`: warning rule and heading `Limited reconstruction evidence`.
- `poor`: danger rule and heading `This result has weak camera coverage`.
- Always show facts: `7 of 42 views registered` and `31 reliable tracks`.
- Explain consequence without blaming the renderer: `The viewer can orbit normally, but areas outside the registered camera arc may stretch or ghost.`
- One secondary action opens `How to recapture`.

Capture guidance is concrete:

1. photograph a complete arc at two heights with 60% to 80% overlap;
2. use at least 30 sharp views for a small object;
3. keep focus, exposure, lighting, and subject position fixed;
4. use diffuse light and reduce reflections where safe;
5. transparent acrylic, mirrors, glossy black surfaces, thin fibers, and moving detail reconstruct poorly; use a removable matte scanning spray only when safe for the object;
6. add a textured, non-reflective background when the subject lacks trackable detail.

The current 7-view, 31-track example must render as `poor`. Its warning copy must specifically separate working orbit controls from unreliable geometry.

## Flow 4: Download and Reuse Results

- Result view shows one primary recommended artifact based on job type: textured bundle for mesh, final PLY for splat.
- Files view is a ruled table with `Name`, `Type`, `Size`, and `Download`. Do not use an artifact card grid.
- Nested paths remain readable and wrap only at separators.
- A download is an ordinary same-origin link and works without JavaScript.
- Missing or deleted files remain listed only if the API marks them unavailable; disable download with a reason.
- Large files include size before action. The browser owns download progress unless the platform exposes it.

## Complete State Model

| region | loading | empty | partial/stale | success | error |
|---|---|---|---|---|---|
| Server | labeled `Connecting` | n/a | `Last update 2m ago` | `Server online` with labeled icon | `Server unavailable`; retain cached queue visually marked stale |
| Queue | six fixed-height skeleton rows, no shimmer | plain copy and `New reconstruction` | last known jobs with stale timestamp | ruled selectable rows | inline retry; do not replace detail if cached data exists |
| Upload | aggregate and current-file progress | no photos selected | partial server job, resume guidance | `Job queued` announcement | validation, network, disk, or file-specific cause |
| Selected job | heading skeleton and rail outline | `Select a job` | cached detail with timestamp | state-specific default view | not found or refresh error with queue retained |
| Rail | known completed stages retained | no stage data, labeled `Waiting for stage data` | indeterminate current stage | all stages complete | failed stage named with Activity link |
| Diagnostic | `Checking capture evidence` | no metrics, omit region | incomplete metrics show available facts | compact good metadata | diagnostics endpoint failure never blocks viewer |
| Viewer | poster plus measured load state | no splat artifact | first frame available while server drops | inactive or active interactive result | unsupported, missing, decode, render, or framing error as distinct states |
| Activity | `Loading activity` | `No log output yet` | follow paused or disconnected | live appended output | retain last output and offer retry |
| Files | `Loading files` | `No result files yet` | unavailable row state | direct downloads | list refresh failure with retry |

### Refresh, Back, Offline, and concurrency

- Refresh reconstructs selected job and view from URL. Uploading jobs explain that local file selection must be restored.
- There is no application session expiry in this version. If future auth returns 401, preserve return URL and do not discard an in-memory file selection before warning the user.
- Polling pauses while the tab is hidden and resumes immediately on visibility change.
- If a job changes state while selected, announce only major transitions: queued to running, stage changes, completed, failed, cancelled. Do not announce every percentage.
- If two tabs request cancel, the later response renders the server's current state without an error toast.
- Offline actions are not queued. Disable cancel and create, preserve reading and viewer interaction, and clearly state that reconnection is required.

## Component Specifications

### Component: UtilityHeader

**Purpose:** maintain product identity, server reachability, and one global action.

- Props/data: connection state, last successful poll, new-job handler.
- States: connecting, online, stale, offline. Use labeled Octicons, never a decorative dot.
- Primary action: `New reconstruction`; hide it while the drawer is already open.
- Desktop: one line, sticky at layer `sticky`. Mobile: product name, status icon with accessible label, and full 48px new action.
- Keyboard: skip link precedes header; normal tab order; focus uses `accent` ring.
- Screen reader: connection status uses polite live updates only when state changes.

### Component: NewReconstructionDrawer

**Purpose:** contain job setup without displacing the inspection workspace.

- Build on Primer drawer/dialog primitives with actual focus trap, inert background, Escape close, and return focus.
- Variants: desktop drawer, mobile full-screen sheet.
- States: pristine, invalid, files selected, uploading, resumable, success, server error.
- Closing during upload uses a Primer confirmation dialog; ordinary close does not.
- The title is display-md. Group headings are ordinary headings, not decorative kickers.

### Component: PhotoDropField

**Purpose:** select and review a photo set.

- Inputs: accepted types, files, rejected files, received filenames/count, upload state.
- Contact sheet uses a ruled grid of square thumbnails and filename utility text. It is not a card collection.
- Keyboard: visible native file input action; Enter/Space opens picker; Clear is separate; do not rely on drag and drop.
- Announce added count, rejected count, and duplicate count in a polite live region.
- Mobile drop target minimum is 96px tall; all actions 48px.

### Component: QueueRail and JobRow

**Purpose:** scan job state and change context quickly.

- Job row data: name, kind, state, stage, progress, created time, stale flag.
- Row height remains stable across polling. Name is one line on desktop, two on mobile; no metadata jump.
- Selected state uses control border plus a 3px accent edge. It does not use a tinted card glow.
- States: loading skeleton, empty, selected, active, completed, failed, stale, keyboard focus.
- Implement as a labeled list of buttons or links. Up/Down changes focus within the rail; Enter selects. Tab leaves the rail.

### Component: ReconstructionRail

**Purpose:** make a long-running native pipeline intelligible.

- Inputs: job kind, stages, current index, state, progress.
- Horizontal at desktop, vertical compact list at mobile.
- Each stage contains icon, label, and state text available to assistive technology.
- Stage transitions use `base` motion only; reduced motion updates immediately.
- A failed segment links to Activity with the relevant log position when available.

### Component: CaptureQualityDiagnostic

**Purpose:** distinguish weak input geometry from a broken viewer.

- Variants: good metadata, limited warning, poor warning, unavailable.
- Inputs: diagnostics object and capture-guide action.
- It uses a ruled strip with semantic leading border and factual values. Never modal on page load.
- The expanded guide is a Primer disclosure, keyboard operable with `aria-expanded` and `aria-controls`.
- Screen reader reads heading, metrics, consequence, then recovery action.

### Component: CalibratedViewport

**Purpose:** host and explain the same-origin SuperSplat viewer without making the page feel fixed or trapping scroll.

- Inputs: job name, content URL, settings URL, poster URL, bytes, diagnostics, renderer capability.
- Slots: toolbar, activation scrim, iframe, registration marks, orientation cue, load/status line.
- States: all viewer states in the viewer table above.
- Registration marks are `control-border` at rest and `accent` only while active.
- The activation scrim must not cover the toolbar. Removing it moves focus into the iframe canvas.
- Toolbar uses real Primer buttons with Octicons and text at least on first use. Icon-only collapse is allowed below 400px only with tooltips and accessible names.
- Same-origin bridge owns activation, wheel prevention, Escape release, reset/frame, and state announcements. Do not rely on hover-only help.
- Load iframe lazily only when Result is visible; keep one instance for the selected artifact and destroy it when the job changes.
- If the PLY exceeds a chosen memory guard, warn before activation rather than crashing the tab. Engineering must derive the guard from tested mobile/desktop budgets, then expose it as configuration rather than a hidden magic number.
- Tests include actual pointer drag, wheel zoom without parent scroll, Escape release, touch activation/exit, reset framing, full screen, unsupported WebGL/WebGPU, slow load, and corrupt PLY.

### Component: ActivityLog

**Purpose:** expose machine output without making raw logs the primary experience.

- Inputs: line batches, cursor, follow preference, loading/error state.
- Toolbar: Follow checkbox, Copy log, Download log if artifact exists.
- Utility typography only. Preserve lines; never reverse newest-first.
- `role=log`, `aria-live=polite`, `aria-relevant=additions text`; pause announcements when Follow is off.
- Do not steal focus or auto-scroll if the user has scrolled away from the tail.
- Cap rendered DOM lines and retain full text separately to avoid browser slowdown.

### Component: ArtifactTable

**Purpose:** identify and download original outputs.

- Columns: Name, Type, Size, Download. At mobile, each row becomes a two-column definition layout with one full-width download action.
- States: loading rows, empty, available, unavailable, refresh error.
- Links use native download behavior and meaningful filenames. No JavaScript-only download buttons.
- Long paths wrap at `/`; sizes use tabular utility numerals.

### Component: SystemFeedback

**Purpose:** communicate transient success, actionable errors, and destructive confirmation.

- Primer toast for short non-blocking completion; Primer banner for persistent connection/disk errors; Primer dialog for cancel/leave-upload.
- Toasts persist at least 5 seconds and can be dismissed. Errors that require action never exist only in a toast.
- All focus return targets are specified. No modal opens solely because a job completed.

## API and Artifact Additions

Preserve existing `/api/v1` endpoints. Add only what the interface needs:

1. Job detail may include the optional `capture_diagnostics` object defined above.
2. Completed splat artifacts include a generated `viewer-settings.json` and optional poster image, each listed by the artifacts endpoint.
3. Uploading job detail exposes received image names or, at minimum, count and bytes so reselected uploads can report resume progress.
4. Error responses should include machine `code` plus plain `error`, for example `disk_full`, `unsupported_file`, `artifact_missing`, without breaking current clients.
5. Viewer and artifact URLs stay same-origin and require no CDN.

No telemetry or external analytics is required for this personal service. Validate usability through local logs and tests instead.

## Engineering Handoff

### Target

Delegate to `react-vite-tailwind-engineer` in static-SPA mode.

Chosen system: **Primer CSS + Octicons**. Bundle pinned dependencies and local fonts into the Nix build. Theme Primer using the locked semantic tokens. Keep the Python service as the runtime server and API. A browser must never contact npm, a font host, analytics, or a CDN.

**Implement exactly this spec. Theme the design system with our locked tokens; do NOT redesign or re-implement its components.**

If retaining plain HTML and JavaScript rather than introducing Vite, use the distributed Primer CSS component classes directly and preserve their semantic markup. The architectural choice must not change behavior or visuals in this spec.

### Implementation sequence

1. Map locked tokens, fonts, Primer, and Octicons into an offline static bundle.
2. Replace the top-heavy card layout with utility header, queue rail, job workspace, views, and new-job drawer.
3. Implement URL state and the complete global/job/upload state model.
4. Add diagnostics payload and per-job viewer settings generation.
5. Implement Calibrated Viewport activation, same-origin scroll/focus bridge, reset framing, help, and full screen.
6. Add Activity and Files views, then responsive and accessibility behavior.
7. Run interaction, API, accessibility, visual, mobile, reduced-motion, and offline tests.

### Acceptance criteria

- [ ] At 1440px the result viewport is the clear focal point and occupies at least 60% of visible job-workspace height for a completed splat.
- [ ] Queue, intake, stage, viewport, log, and files use their specified layout families with no nested cards.
- [ ] Every production visual value maps to `.ulpi/design/DESIGN.md`; off-system values are zero.
- [ ] All controls use Primer and Octicons; the static build has no runtime network dependency.
- [ ] A first-time user can discover orbit, pan, zoom, frame/reset, help, full screen, and viewer exit without guessing.
- [ ] Before viewer activation, wheel scrolls the page. While active, wheel zooms the viewer without moving the page. Escape restores page scrolling and focus.
- [ ] Real pointer orbit, touch interaction, reset/frame, full screen, and PLY loading are covered by browser tests.
- [ ] The 7-view, 31-track fixture shows a poor-quality diagnostic and explains that stretched geometry is capture quality, not failed camera control.
- [ ] Transparent, reflective, thin, low-texture, and moving-subject capture tips are available without blocking the viewer.
- [ ] Upload resumes in-page after a network failure and gives precise reselection guidance after refresh.
- [ ] URL state survives refresh and Back for selected job, view, and new-job drawer.
- [ ] All loading, empty, stale, partial, success, cancelled, interrupted, offline, unsupported-renderer, disk-full, missing-artifact, decode, and server-error states match this spec.
- [ ] Keyboard navigation reaches every application action; focus is always visible; dialogs trap and restore focus; status changes are announced without percentage spam.
- [ ] WCAG AA automated tests pass and manual contrast checks match the recorded ratios.
- [ ] Mobile controls are at least 48px, safe areas are respected, viewer has a persistent Exit action, and no scroll trap remains.
- [ ] Reduced motion disables drawer/viewer-help transitions and any camera reset animation.
- [ ] Existing backend unit tests pass; new diagnostics/framing tests and full browser interaction tests pass.

## Design Pre-Flight, Final Run

Run date: 2026-08-26.

### Identity lock

- [x] Off-system design values specified: **0**. All values resolve to `DESIGN.md`.
- [x] Accent count: **1**. Radius scales: **1**. Icon families: **1**. Type pairings: **1**.
- [x] Utility header, drawer, queue, rail, viewport, activity, and files read as the same instrument workspace.
- [x] `DESIGN.md` was written and re-read against this feature before handoff.

### Anti-slop

- [x] Banned primary fonts: **0**. Purple/blue glow: **0**. Cream default: **0**. Gradient text: **0**.
- [x] Three-equal-card layouts: **0**. Nested cards: **0**. Numbered eyebrows: **0**. Centered dark hero: **0**.
- [x] Visible buzzwords: **0**. Fake names: **0**. Fake filler numbers: **0**. Stylistic em dashes: **0**.
- [x] The slop test passes: calibration framing, stage rail, and reconstruction diagnostics are tied to this machine task.
- [x] The counterfactual test passes: this is not a reusable dark SaaS dashboard.
- [x] The Calibrated Viewport is present and carries the product signature.
- [x] Inspiration synthesis is not applicable; no links were supplied.

### State, accessibility, layout, and cognition

- [x] Every interactive region specifies loading, empty, partial or stale, success, and error behavior.
- [x] Refresh, Back, future session expiry, duplicate tabs, hidden tab, and offline behavior are covered.
- [x] WCAG ratios are recorded, visible focus and keyboard paths are specified, ARIA/live behavior is named, and reduced motion is mandatory.
- [x] Mobile targets are at least 48px and safe areas are required.
- [x] Distinct layout families: **5**, exceeding the required 3.
- [x] Top-level workspace actions remain under 5, each form group remains at 4 controls or fewer, and each view has one primary action.

### Scored self-critique

| axis | score, 0 to 4 | reason |
|---|---:|---|
| Distinctiveness | 4 | calibrated viewport and reconstruction evidence are specific to photogrammetry |
| Hierarchy and focus | 4 | completed result owns the workspace; queue and machine detail recede |
| Consistency | 4 | tokens, system, icons, shape, voice, and motion are locked once |
| Accessibility | 4 | focus and scroll ownership, non-visual alternatives, status announcements, contrast, and mobile targets are explicit |
| State and edge coverage | 4 | state matrix covers upload, queue, pipeline, diagnostics, viewer, logs, files, offline, and refresh |
| Copy quality | 3 | plain and concrete, with final in-product proofreading still required |
| Restraint | 4 | boldness is spent only on the functional viewport signature |
| Motion motivation | 3 | all motion has a reason, but camera-transition feel requires implementation testing |
| **Total** | **30 / 32** | no axis is 2 or below |

### Revise and justify

- Raised the interactive boundary token from the current low-contrast rule to `control-border` so all focus-adjacent UI clears 3:1.
- Replaced the current implicit viewer hint with explicit activation, help, frame/reset, full-screen, and Escape behavior because successful drag still felt fixed before interaction.
- Added scroll ownership and a mobile Exit action because wheel and touch behavior must never trap page navigation.
- Added capture diagnostics and the 7-view, 31-track fixture because severe ghosting can otherwise be misdiagnosed as a camera-control defect.
- Replaced artifact cards with a table and moved setup into a drawer because the viewport, not repeated containers, must remain the focal point.

All pre-flight boxes pass. No critique axis requires another revision.
