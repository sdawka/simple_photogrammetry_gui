---
project: servOS Photogrammetry Workspace
register: product
aesthetic_direction: industrial / signage
color_strategy: restrained
design_system: Primer CSS + Octicons
design_variance: 7
motion_intensity: 3
visual_density: 7
---

# Locked Design Language

## Design Read

A calibrated reconstruction bay for a home GPU workstation: warm, legible instruments surrounding one vivid cyan working viewport.

The industrial/signage direction comes from the actual task. This is a long-running machine process that receives photographs, moves through named stages, and produces inspectable geometry. It should feel closer to a well-labeled imaging instrument than to a generic SaaS dashboard. The counterfactual test passes because the calibration marks, reconstruction rail, camera language, and warm Gruvbox-derived graphite are specific to photogrammetry rather than reusable dashboard decoration.

## Register and System

This is a `product` interface. Clarity, continuity, and recoverability take precedence over brand spectacle.

- Build on **Primer CSS** controls and interaction patterns. Use **Octicons** as the only icon family.
- Bundle Primer and fonts into the static build. The service must remain fully usable without internet access and must not require Node at runtime.
- Theme Primer with the tokens in this file. Do not hand-recreate its buttons, form controls, dialogs, tooltips, or focus behaviors.
- Preserve the backend's static-file delivery and same-origin API contract. A build step is allowed only if Nix produces the final static assets reproducibly.

## Signature: Calibrated Viewport

The memorable element is the **Calibrated Viewport**: the selected result occupies a ruled, edge-to-edge inspection frame with four restrained corner registration marks, a compact XYZ orientation cue, a visible camera-help affordance, and the reconstruction stage rail directly above it.

The marks are functional. They establish the area that accepts orbit, pan, and zoom input and distinguish the rendered scene from a screenshot. They never animate continuously and never appear on ordinary panels. The same one-pixel ruled language appears in the upload contact sheet and stage rail, tying capture, processing, and inspection together.

Spend visual boldness here only. Surrounding queue, artifacts, and logs use flat surfaces and dividers.

## Color, Locked

The palette is a re-derived dark Gruvbox family: graphite surfaces tinted warm, readable wheat text, and one cyan accent taken from imaging and GPU instrumentation. Use approximately 60% background, 30% surfaces, and no more than 10% accent. No gradients, glow, glassmorphism, pure black page fields, or accent-colored decoration.

| role | OKLCH | hex | use | minimum recorded contrast |
|---|---:|---:|---|---:|
| `background` | `0.241 0.005 219.7` | `#1D2021` | page and full-screen viewer surround | n/a |
| `surface` | `0.277 0.000 89.9` | `#282828` | workspace regions, drawer body | n/a |
| `elevated` | `0.311 0.003 48.6` | `#32302F` | menu, dialog, toast, active row | n/a |
| `surface-quiet` | `0.344 0.007 48.5` | `#3C3836` | inactive control fill, log well | n/a |
| `text` | `0.922 0.055 92.5` | `#F2E5BC` | headings and primary copy | 13.05:1 on background; 11.73:1 on surface |
| `text-muted` | `0.773 0.040 86.9` | `#C0B499` | descriptions and secondary metadata | 7.99:1 on background; 7.18:1 on surface |
| `text-subtle` | `0.660 0.038 85.4` | `#9D9178` | timestamps and tertiary labels | 5.27:1 on background; 4.74:1 on surface |
| `divider` | `0.411 0.012 51.9` | `#504945` | decorative rules only, never sole control boundary | 1.86:1 on background |
| `control-border` | `0.572 0.021 70.0` | `#80766B` | input, button, focus-adjacent and viewport boundaries | 3.69:1 on background; 3.31:1 on surface |
| `accent` | `0.688 0.068 198.1` | `#65A8AA` | primary action, selected state, focus ring, progress | 6.04:1 against background; 5.43:1 against surface |
| `success` | `0.765 0.158 110.8` | `#B8BB26` | completed state only | 7.94:1 on background; 7.14:1 on surface |
| `warning` | `0.832 0.159 83.0` | `#FABD2F` | queued, interrupted, disk warning | 9.67:1 on background; 8.69:1 on surface |
| `danger` | `0.708 0.184 29.7` | `#FF6B58` | failure, destructive action, validation | 5.85:1 on background; 5.26:1 on surface |
| `info` | same as `accent` | `#65A8AA` | neutral system information | same as accent |
| `ink-on-accent` | same as `background` | `#1D2021` | text and icon on filled accent | 6.04:1 |

Rules:

- `accent` is the only interaction color. Semantic colors communicate real state and are never used decoratively.
- Text never uses `divider` or `control-border` colors. `text-subtle` is the lowest-contrast text role.
- A non-text control boundary uses `control-border`, not `divider`, to retain at least 3:1 contrast.
- Viewer canvas may use `#111111` internally because it is rendered content, not an application surface. All application chrome remains tokenized.

## Type, Locked

Bundle local WOFF2 subsets. No network font requests.

| role | family | use | notes |
|---|---|---|---|
| `display` | Barlow Condensed, weight 600 | view title, job title, large progress value | industrial signage voice; use only at 24px and above; tracking `-0.015em` floor |
| `body` | Atkinson Hyperlegible Next, weights 400/600/700 | controls, instructions, readable copy | humanist contrast to condensed display; 65 to 72ch maximum reading measure |
| `utility` | IBM Plex Mono, weights 400/500 | stages, timestamps, file sizes, logs, camera legend | tabular numerals enabled; uppercase only for short machine states |

Fallbacks are `Aptos Narrow, sans-serif` for display, `Aptos, sans-serif` for body, and `ui-monospace, monospace` for utility. Fallbacks are not the primary identity.

Type scale:

- `display-lg`: 40/40, display 600, desktop workspace title only; 32/34 below `md`.
- `display-md`: 30/32, display 600, selected job or drawer title; 26/28 below `md`.
- `heading`: 20/26, body 700.
- `body`: 15/23, body 400.
- `body-strong`: 15/23, body 600.
- `utility`: 12/18, utility 500.
- `micro`: 11/16, utility 500. Never use below 11px.

Use `text-wrap: balance` on short headings and `text-wrap: pretty` on explanatory copy. Do not use stylized eyebrow kickers above headings.

## Structural Scales, Locked

### Spacing

One 4px rhythm: `0, 4, 8, 12, 16, 20, 24, 32, 40, 48, 64, 80` px.

No component may introduce an off-scale gap or padding. Dense queue rows use 12px; ordinary controls use 16px; section separation uses 24px or 32px; major workspace separation uses 40px or 48px.

### Radius

One restrained scale: `square 0`, `sm 4`, `md 8`, `lg 12`, `full 9999` px.

- Buttons and fields: `sm`.
- Drawer, dialog, toast: `md`.
- Large empty-state and upload boundaries: `lg`.
- Status tags only: `full`.
- Workspace regions and viewport: `square` or `sm`. Avoid soft card stacks.

### Elevation

- Default hierarchy uses background, surface, and one-pixel rules, not shadows.
- Drawer/dialog: `0 16px 48px rgb(0 0 0 / 0.36)`.
- Toast/menu: `0 8px 24px rgb(0 0 0 / 0.28)`.
- No other shadow is permitted.

### Motion

- `fast 120ms`, `base 240ms`, `emphasis 400ms`.
- Easing: `cubic-bezier(0.16, 1, 0.3, 1)`; exits use 75% of the corresponding enter duration.
- Motivated motion only: drawer enters to preserve spatial origin, progress advances to show work, viewer help fades after first interaction.
- No bounce, scale-pop, pulsing status, infinite animation, or parallax.
- Under `prefers-reduced-motion: reduce`, transitions become immediate and camera reset does not animate.

### Grid and Breakpoints

- Breakpoints: `sm 640`, `md 768`, `lg 1024`, `xl 1280`, `2xl 1536` px.
- Desktop container: full available width with 32px edge gutters at `lg`, 40px at `xl`, maximum 1600px.
- Workspace grid at `lg`: 320px queue rail plus a fluid detail column; at `2xl`, queue may grow to 360px.
- Touch targets are at least 48px on touch layouts and at least 40px on pointer layouts.

### Layers

`base 0`, `dropdown 20`, `sticky 30`, `fixed 40`, `modal-backdrop 45`, `modal 50`, `tooltip 60`, `toast 70`, `skip-link 80`.

## Iconography, Locked

Use Octicons only, at 16px in controls and 24px in empty states. Icons support a text label unless the action is universally understood and has an accessible name. Do not use Unicode arrows, geometric glyphs, decorative status dots, emoji, or mixed icon styles. The XYZ orientation cue and viewport registration marks are diagrammatic data, not members of the icon family.

## Voice

`register: plain, confident, technical`

- Describe what the machine is doing now: “Matching photos”, “Waiting for GPU”, “Building splat”.
- Prefer concrete nouns: photos, mesh, splat, job, file, GPU, result.
- One action name remains consistent through its state: `Create job` to `Job created`; `Cancel job` to `Job cancelled`; `Download result` to `Downloaded`.
- Errors state cause, consequence, and next action. Example: “Upload stopped after 18 photos. Select the same files to resume.”
- Avoid buzzwords, exclamation marks, fake names, unexplained acronyms, and stylistic em dashes.

## Cross-Session Consistency

Every screen must read as the same product if placed side by side.

On every later feature, read this file first. Use only these tokens, Primer CSS patterns, Octicons, and the Calibrated Viewport signature. If a new requirement cannot fit, update this file deliberately before writing a feature spec or production code.
