---
feature: learned photo matching and viewer camera modes
status: locked for implementation
binds_to: .ulpi/design/DESIGN.md
target_agent: frontend-engineering
design_system: Primer CSS + Octicons
delivery: offline static SPA served by the existing Python service
---

# Learned Matching and Camera Modes

Every screen must read as the same product if placed side by side.

Read `.ulpi/design/DESIGN.md` and `.ulpi/design/photogrammetry-workspace.md` before implementation. This feature extends the existing industrial/signage workspace and Calibrated Viewport. It introduces no new colors, type roles, spacing, radii, icons, shadows, or motion values.

## Design Read

Expose two expert choices as plainly labeled instrument controls: one capture-processing decision before a job starts and one camera-state decision while inspecting a result.

The committed aesthetic remains **industrial / signage**. Matching method is presented as a reconstruction instrument setting, while camera mode is a two-position viewport control. Neither becomes a decorative card, novelty toggle, or generic settings screen.

## Goals

1. Let the operator choose learned matching when standard SIFT leaves related photos unregistered.
2. Explain the time and hardware tradeoff before upload without requiring knowledge of ALIKED or LightGlue.
3. Let the operator switch in one action between the generated fixed point of view and a freely movable orbit/pan camera.
4. Preserve explicit viewer scroll ownership, keyboard escape, generated camera framing, and offline operation.

## Flow 1: Choose a Matching Method

**Goal:** choose the least expensive matcher likely to register the capture.

```text
[Open New reconstruction]
    -> [Choose output]
    -> [Choose Standard or Learned matching]
    -> [Read method-specific guidance]
    -> [Choose photos]
    -> [Create job]
```

### Matching method control

Add one `Matching` fieldset after `Output` and before `Quality`.

- `Standard`: selected by default. Supporting copy: `Fastest. Best for textured subjects with strong overlap.`
- `Learned`: supporting copy: `Slower. Helps connect difficult views when standard matching misses them.`
- A compact method note below the fieldset updates with the selection. For Learned: `Runs feature matching on CPU on this server. Gaussian training still uses the GPU.`
- Do not expose model names in the primary labels. The Activity log may name `ALIKED` and `LightGlue` because it is technical evidence.
- The choice applies to both mesh and splat jobs and is sent as `settings.feature_matcher`.

### States and errors

| State | Presentation | Behavior |
|---|---|---|
| Default | Standard selected | Job payload uses `exhaustive_matcher` |
| Learned selected | Learned selected and CPU note visible | Job payload uses `learned_matcher` |
| Uploading | Selection remains visible but disabled with other job fields | No mid-upload matcher changes |
| Unsupported backend | Drawer error: `This server does not include learned matching.` | Preserve files and allow Standard retry |
| Learned stage failed | Activity names the failed learned-feature or learned-match stage | Existing checkpoint retry rules apply |
| Refresh | Existing job detail reports its stored matcher | New drawer defaults to Standard |
| Offline | Both choices disabled with other create controls | Preserve in-memory selection until reconnect |

### Accessibility

- Use a native radio group with `fieldset` and `legend`.
- Tab enters the group; arrow keys select; the supporting note is referenced with `aria-describedby`.
- Selection is expressed by checked state and text, not color.
- Touch targets remain at least 48px below `md` and 40px on pointer layouts.

## Flow 2: Switch Viewer Camera Mode

**Goal:** compare a stable authored view with free inspection without wondering whether the camera is stuck.

```text
[Completed splat opens at generated POV]
    -> {Fixed view | Free camera}
       Fixed view -> [No camera gestures; page scroll owns wheel]
       Free camera -> [Focus canvas; orbit, pan, and zoom]
       Free camera -> Fixed view -> [Restore generated POV and page scroll]
```

### Camera mode control

Place a labeled two-option segmented radio group in the Calibrated Viewport toolbar, before `Controls` and `Full screen`:

- `Fixed view`: default on initial load and after selecting another job. Restores the generated camera position and target from `viewer-settings.json`, disables orbit/pan/zoom input, and leaves wheel gestures with the page.
- `Free camera`: activates the same-origin canvas, focuses it, and enables orbit, pan, and zoom. The existing accent viewport boundary and `Exit viewer` control appear.

The visible group label is `Camera`. Use Primer segmented-control semantics if bundled; otherwise use the existing radio-control vocabulary and locked tokens. Do not draw an iOS-style sliding switch.

Selecting `Fixed view` while free:

1. releases viewer scroll ownership;
2. restores the generated POV by reloading only the viewer document with the same PLY and settings URLs;
3. shows `Restoring fixed view` until ready;
4. returns focus to the selected `Fixed view` control;
5. announces `Fixed view restored. Page scrolling available.`

Selecting `Free camera`:

1. ensures the iframe is loaded;
2. focuses its canvas;
3. enables pointer and wheel input;
4. announces `Free camera active. Press Escape to return to fixed view.`

`Escape` always selects Fixed view, restores the generated POV, releases scroll ownership, and returns focus to the `Fixed view` control. The persistent active-viewer action reads `Use fixed view`, replacing `Exit viewer` so the destination is explicit.

### Viewer chrome changes

- Remove the separate `Frame result` toolbar action; Fixed view is the framing/reset action.
- Keep `Controls` and `Full screen`.
- In Fixed view, the center scrim is quiet and reads `Fixed viewpoint` with a secondary `Use free camera` action. Its instruction line reads `Switch to Free camera to orbit, pan, and zoom.`
- In Free camera, the scrim is absent. The existing corner registration marks use `accent` and the cursor uses `grab`/`grabbing`.
- Help copy begins with the current mode. Fixed: `Camera movement is locked to the generated capture view.` Free: retain mouse, trackpad, touch, and Escape instructions.
- Full screen preserves the selected mode. Exiting browser full screen returns to Fixed view to avoid a hidden scroll trap.

### Camera-mode states

| State | Fixed view | Free camera |
|---|---|---|
| Loading | selected and disabled; `Loading fixed view` | disabled |
| Ready | selected; rendered frame visible; page owns scroll | available |
| Activating | available | selected and disabled until canvas focus succeeds |
| Active | available | selected; canvas owns pointer/wheel; `Use fixed view` visible |
| Restoring | selected and disabled; `Restoring fixed view` | disabled |
| Renderer unsupported | selected; fixed poster if available | disabled with reason in status line |
| Offline after load | remains rendered | current interaction remains usable; switching Fixed uses cached same-origin assets |
| Job/artifact change | selected and loading the new generated POV | cleared |

### Accessibility and responsive behavior

- Implement the two choices as radios, not `aria-pressed` buttons. Group label: `Camera mode`.
- Arrow keys switch radio selection; Space selects the focused option. Do not require pointer input.
- Status changes use the existing polite viewer status region. Avoid duplicate announcements from the iframe.
- The iframe retains its job-specific accessible title. Direct download remains the non-visual result alternative.
- Below 400px, the group remains text-labeled and spans the toolbar width. `Controls` and `Full screen` occupy the next row.
- All targets remain 48px on touch layouts. Safe-area and reduced-motion requirements remain unchanged.

## Backend and API Contract

- Accept `settings.feature_matcher` values `exhaustive_matcher`, `sequential_matcher`, and `learned_matcher`.
- Learned mode extracts ALIKED features and LightGlue matches, imports them into the normal COLMAP database, runs normal geometric verification, then continues through calibration, mapping, and the existing mesh or splat pipeline.
- Geometric verification thresholds remain conservative. Do not fabricate connections by accepting unverified pairs.
- Use bounded pair discovery: start with adjacent and near-adjacent capture pairs, then match across disconnected verified components until connected or candidates are exhausted. Avoid all-pairs learned inference when the graph is already connected.
- Log the learned model names, execution device, candidate-pair count, verified-pair count, registered-view count, and fallback reason if applicable.
- Never silently fall back from Learned to Standard. A missing learned runtime is an actionable job failure.
- Checkpoints remain resumable. Learned feature extraction and matching use the existing `features` and `matching` checkpoint keys, with method-specific stage labels.
- Keep model weights in the immutable Nix store. Keep runtime caches under `/persist/photogrammetry/cache`; never place keys or required state in `/tmp`.

## Implementation Handoff

### Frontend target

Delegate to `frontend_engineering`. Implement exactly this spec in the existing offline static SPA. Theme controls with the locked tokens; do not redesign or re-implement the rest of the workspace. Use the existing semantic HTML and CSS control vocabulary where Primer assets are not locally bundled.

### Backend target

Delegate pipeline and packaging work to `pipeline_backend`. Preserve Standard as the default and the current pipeline behavior byte-for-byte when it is selected.

### Acceptance criteria

- [ ] New jobs can choose Standard or Learned matching and the selected value reaches the stored API settings.
- [ ] Standard remains the default and existing API clients remain compatible.
- [ ] Learned mode registers all 11 `gauss_attempt` views with normal geometric verification in a clean pipeline run.
- [ ] Learned matching uses CPU on the current servOS package when the CUDA ONNX provider is unavailable; Brush remains on the GTX 1060.
- [ ] Learned weights/caches and job checkpoints survive reboot under Nix store or `/persist`.
- [ ] Fixed view is the initial camera mode and matches generated framing.
- [ ] One click or keyboard selection enters Free camera; orbit and pan visibly change the rendered view.
- [ ] Selecting Fixed view or pressing Escape restores the generated POV and page scrolling.
- [ ] Camera mode remains obvious on desktop and mobile, including while loading or unsupported.
- [ ] Full screen, Controls, direct download, viewer activation, and job switching continue to work.
- [ ] Backend unit tests, frontend DOM/syntax checks, pointer interaction tests, and a live servOS browser test pass.

## Design Pre-Flight

### Identity lock and anti-slop

- [x] Off-system visual values: 0. The feature uses only the locked design language.
- [x] Accent count: 1. Radius scales: 1. Icon families: 1. Type pairings: 1.
- [x] Banned fonts, gradients, glow, nested cards, decorative status dots, and novelty toggles: 0.
- [x] Visible buzzwords, fake names, fake filler metrics, and stylistic em dashes: 0.
- [x] The Calibrated Viewport remains the single signature element.
- [x] The counterfactual and slop tests pass because both controls are specific reconstruction instruments.

### State, accessibility, layout, and cognition

- [x] Loading, default, active, restoring, unsupported, offline, refresh, and failure states are specified.
- [x] Native radio semantics, keyboard operation, focus return, live announcements, contrast, touch targets, and reduced motion are covered.
- [x] Existing five layout families remain unchanged.
- [x] Matching offers two choices. Camera mode offers two choices. The viewer toolbar retains at most four decision groups.

### Scored self-critique

| axis | score | reason |
|---|---:|---|
| Distinctiveness | 4 | controls read as matching and camera instruments inside the existing reconstruction bay |
| Hierarchy and focus | 4 | the splat remains the focal point; controls stay in its quiet toolbar |
| Consistency | 4 | no token, system, icon, or voice drift |
| Accessibility | 4 | native radio semantics, focus restoration, scroll ownership, and announcements are explicit |
| State and edge coverage | 4 | learned-runtime and camera-transition failures have recoveries |
| Copy quality | 3 | plain user labels with technical names confined to Activity |
| Restraint | 4 | one new fieldset and one compact two-position control only |
| Motion motivation | 3 | only viewer restoration uses existing load-state motion |
| **Total** | **30 / 32** | no axis is 2 or below |

No pre-flight revision was required.
