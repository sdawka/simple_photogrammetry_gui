---
feature: learned photo matching and viewer camera control
status: locked for implementation
binds_to: .ulpi/design/DESIGN.md
target_agent: frontend-engineering
design_system: Primer CSS + Octicons
delivery: offline static SPA served by the existing Python service
---

# Learned Matching and Viewer Camera Control

Every screen must read as the same product if placed side by side.

Read `.ulpi/design/DESIGN.md` and `.ulpi/design/photogrammetry-workspace.md` before implementation. This feature extends the existing industrial/signage workspace and Calibrated Viewport. It introduces no new colors, type roles, spacing, radii, icons, shadows, or motion values.

## Design Read

Expose the matching choice as a plainly labeled instrument control, then make splat inspection a single, understandable interaction rather than a mode choice.

The committed aesthetic remains **industrial / signage**. Matching method is presented as a reconstruction instrument setting, while the camera uses direct actions for exploration, input release, and reset. Neither becomes a decorative card, novelty toggle, or generic settings screen.

## Goals

1. Let the operator choose learned matching when standard SIFT leaves related photos unregistered.
2. Explain the time and hardware tradeoff before upload without requiring knowledge of ALIKED or LightGlue.
3. Let the operator freely orbit and pan from the generated starting angle without choosing a camera mode.
4. Preserve explicit viewer scroll ownership, keyboard escape, an intentional reset action, generated camera framing, and offline operation.

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

## Flow 2: Explore the Splat

**Goal:** inspect the splat freely while keeping page scrolling and camera reset predictable.

```text
[Completed splat opens at generated POV]
    -> [Explore in 3D]
    -> [Focus canvas; orbit, pan, and zoom]
    -> Escape or Exit viewer -> [Release input; preserve camera position]
    -> Reset camera -> [Return to generated POV]
```

### Camera actions

Place `Reset camera` in the Calibrated Viewport toolbar before `Controls` and `Full screen`. It is an ordinary action, not a mode:

- The first rendered frame uses the generated camera position and target from `viewer-settings.json`.
- `Explore in 3D` activates the same-origin canvas, focuses it, and enables orbit, pan, and zoom. The existing accent viewport boundary and `Exit viewer` control appear.
- `Escape` and `Exit viewer` release pointer and wheel ownership to the page without reloading the viewer or changing its camera position. Reactivating resumes from the same position.
- `Reset camera` reloads only the viewer document with the same PLY and settings URLs. If the viewer was active, interaction resumes after the first reset frame; otherwise page scrolling remains in control.

There is no persistent camera-mode selector. The generated POV is only a starting angle, not a locked viewing mode.

Resetting the camera:

1. releases viewer scroll ownership;
2. reloads only the viewer document with the same PLY and settings URLs;
3. shows `Resetting camera` until ready;
4. restores the prior input-ownership state;
5. announces that the camera reset completed.

Activating the viewer:

1. ensures the iframe is loaded;
2. focuses its canvas;
3. enables pointer and wheel input;
4. announces `Interactive viewer active. Press Escape to return to page scrolling.`

`Escape` only releases viewer input, preserves the current camera position, and returns focus to `Explore in 3D`. The persistent active-viewer action reads `Exit viewer`.

### Viewer chrome changes

- Keep `Reset camera`, `Controls`, and `Full screen` as the only viewer toolbar actions.
- Before activation, the center scrim reads `Interactive camera` with an `Explore in 3D` action and concise gesture guidance.
- During interaction, the scrim is absent. The existing corner registration marks use `accent` and the cursor uses `grab`/`grabbing`.
- Help copy explains mouse, trackpad, touch, and that Escape releases input without moving the camera.
- Exiting browser full screen releases viewer input to avoid a hidden scroll trap without resetting the camera.

### Viewer camera states

| State | Presentation | Behavior |
|---|---|---|
| Loading | `Loading interactive result`; actions disabled | page owns scroll |
| Ready | generated starting angle; `Explore in 3D` visible | page owns scroll |
| Active | accent boundary; `Exit viewer` visible | canvas owns pointer and wheel |
| Released | activation scrim returns over the current camera position | page owns scroll; reactivation resumes position |
| Resetting | `Resetting camera`; reset and activation disabled | viewer reloads generated POV |
| Renderer unsupported | failure reason and enabled Reset action | direct download remains available |
| Offline after load | rendered frame and current interaction remain usable | reset uses cached same-origin assets |
| Job/artifact change | new generated POV loads | previous input state is cleared |

### Accessibility and responsive behavior

- Use native buttons for the direct `Explore in 3D`, `Exit viewer`, and `Reset camera` actions.
- All actions remain keyboard reachable and expose their disabled state while the renderer loads.
- Status changes use the existing polite viewer status region. Avoid duplicate announcements from the iframe.
- The iframe retains its job-specific accessible title. Direct download remains the non-visual result alternative.
- Below 400px, the three toolbar actions share one equal-width row.
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
- [ ] The initial frame matches the generated camera framing without presenting it as a mode.
- [ ] One click or keyboard activation enters the interactive viewer; orbit and pan visibly change the rendered view.
- [ ] Escape or Exit viewer restores page scrolling without changing the camera position; reactivation resumes that position.
- [ ] Reset camera returns to the generated POV and preserves whether the viewer was active.
- [ ] Camera actions remain obvious on desktop and mobile, including while loading or unsupported.
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

- [x] Loading, ready, active, released, resetting, unsupported, offline, refresh, and failure states are specified.
- [x] Native button semantics, keyboard operation, focus return, live announcements, contrast, touch targets, and reduced motion are covered.
- [x] Existing five layout families remain unchanged.
- [x] Matching offers two choices. Camera behavior uses direct actions rather than modes. The viewer toolbar retains three actions.

### Scored self-critique

| axis | score | reason |
|---|---:|---|
| Distinctiveness | 4 | controls read as matching and camera instruments inside the existing reconstruction bay |
| Hierarchy and focus | 4 | the splat remains the focal point; controls stay in its quiet toolbar |
| Consistency | 4 | no token, system, icon, or voice drift |
| Accessibility | 4 | native control semantics, focus restoration, scroll ownership, and announcements are explicit |
| State and edge coverage | 4 | learned-runtime and viewer-transition failures have recoveries |
| Copy quality | 3 | plain user labels with technical names confined to Activity |
| Restraint | 4 | one matching fieldset and three direct viewer actions only |
| Motion motivation | 3 | only viewer restoration uses existing load-state motion |
| **Total** | **30 / 32** | no axis is 2 or below |

No pre-flight revision was required.
