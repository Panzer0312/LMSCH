# Phase 3: Video setup page

**Goal:** a dedicated page (`/video`) for getting the picture perfectly onto the scrim, window or
wall. Geometry and image settings are always applied to everything mpv plays (main, idle, patterns).
Display profiles handle videos that need different framing. Every change shows live on the projector.

## Mechanism (decided)

All adjustments are mpv runtime properties set over IPC. No re-encode and no restart needed.

| Adjustment | mpv property / mechanism |
|---|---|
| Zoom, pan | `video-zoom`, `video-pan-x`, `video-pan-y` |
| Stretch (independent X/Y) | `video-scale-x`, `video-scale-y` |
| Alignment in frame | `video-align-x`, `video-align-y` |
| Rotation 0/90/180/270 | `video-rotate` |
| Aspect override | `video-aspect-override` |
| Crop source | `video-crop` |
| Image | `brightness`, `contrast`, `gamma`, `saturation` |
| Keystone (4-corner warp), flip H/V, edge mask with feather | user GLSL shader `player/shaders/warp.glsl` loaded at mpv start via `--glsl-shaders`, parameters via `//!PARAM` and `change-list glsl-shader-opts set k=v` |

Verified on dev mpv 0.41: all properties above exist. `//!PARAM` shaders need `vo=gpu-next`
(plain `gpu` ignores them); crop applies to the coded frame before rotation.

- [ ] **SPIKE** Hub mpv version (Debian 13) supports `video-scale-x/y`, `video-crop`, `glsl-shader-opts`.
- [ ] **SPIKE** Shader with `//!PARAM` works with `--vo=gpu` + DRM + hwdec on the hub GPU, cost < 2 ms/frame at 1080p.
  If `vo=gpu-next` is needed, note it in `decisions.md`.

## 3.1 Data model

- [x] `DisplayProfile` table: id, name, `geometry` JSON (`DisplayGeometry` Pydantic model: all fields
  above, `corners` as 4 normalized (x, y) offsets, `mask` {top, right, bottom, left, feather}, flip_h, flip_v).
- [x] `RuntimeSettings.display_profile_id` = global default ("Projector setup").
- [x] Optional override `Media.display_profile_id` (per video, e.g. letterboxed or 4:3 source) and
  `Profile.display_profile_id` (per show). Precedence: profile > media > global. Migration.
- [x] Included in profile export/import by name; included in backups automatically (DB).

## 3.2 Player integration

- [x] `player/geometry.py`: pure `DisplayGeometry → (props: dict, shader_opts: dict)`. Unit-tested,
  including identity → mpv defaults.
- [x] `MpvPlayer.apply_geometry(g)`: sets only changed props; called on start/restart and after every
  `load()` with the resolved profile. Per-load hook is reused by phase 4.
- [x] Debounced live apply (≤ 30 ms coalescing) so slider drags don't flood IPC.
- **Accept:** resolved geometry is applied to main, idle and pattern; e2e test with `vo=null` reads back props.

## 3.3 Video setup page (`/video`)

- [x] **Setup mode:** entering stops any show (same rule as test pattern), leaving restores the active profile idle state. Refused while blackout.
- [x] Source picker: alignment pattern (3.4), any media file (looped, scrub slider), or the active profile's idle video.
- [x] Controls grouped: Frame (zoom, pan, stretch X/Y, align, rotate, aspect, crop) · Keystone · Mask · Image.
  Sliders + numeric fields + reset per group. Arrow keys nudge the selected control (Shift = fine).
- [x] **Keystone editor:** browser preview canvas showing the output frame; drag the 4 corner
  handles, live on projector. Numbered corners match the corner-marker pattern.
- [x] **Browser preview:** WebGL canvas applying the same transform to a frame of the selected source
  (thumbnail or `<video>`), with output bounds outline. Pure mimic; the projector stays the truth.
- [x] Display profile bar: select, save, save as, rename, delete, "set as default", "use for this video".
- [x] A/B toggle: flip between saved and edited values to compare on the projector.
- [x] Usable on phone (stand at the scrim, adjust from the phone): controls stack, preview collapses.
- **Accept:** from the phone, align a video onto a skewed window frame using corners + mask, save,
  and the next triggered show uses it.

## 3.4 Alignment and scanning patterns

Generated with ffmpeg once, cached in data dir (like `show/testpattern.py`), shown through the same
geometry so the corrected result is what you see. Still images as PNG, moving ones as short looped H.264.

| Pattern | Use |
|---|---|
| Grid (existing) | General keystone and focus |
| Numbered corner markers + border | Keystone corner dragging |
| Crosshair + concentric circles | Aspect and stretch (circles must be round) |
| Edge chase (bars running along each edge) | Find the usable surface edges and set mask |
| Scan line sweep, horizontal and vertical | Find surface extent and dead zones on scrim or window |
| Focus (fine line pairs, star) | Lens focus |
| Grey ramp + near-black steps | Set brightness/gamma so black is truly invisible on the scrim |
| Checkerboard, solid white, solid black | Hot spots, light leaks, projector black level |
| Safe-area overlay of the current mask | Check mask edges with real content |

- [x] `show/patterns.py` replaces `testpattern.py` (keep the old endpoint working), pattern registry with id, label, kind.
- [x] Pattern picker on `/video` and in `/setup` projector step.
- **Accept:** each pattern renders at the output resolution and respects geometry and mask.

## 3.5 Content helpers

- [x] Media page shows a warning badge when a video's aspect differs from the default display profile, with "create display profile for this video".
- [x] "Black check": scan a video's frames (ffmpeg `signalstats`, sampled) and warn when the background is not near black (light spill on scrim).

## 3.6 README: Debian install requirements (last task of the phase)

- [x] README section "Debian 13 hub: required packages", one table: package, why, installed by
  `install.sh` yes/no. Covers everything up to and including phase 3: mpv (minimum version from the
  3.0 spike), ffmpeg, python3-venv, avahi-daemon, libnss-mdns, alsa-utils, VA-API driver, vainfo,
  Mesa GPU drivers needed by the warp shader (`libgl1-mesa-dri`, `libegl1`, `mesa-vulkan-drivers` if
  `gpu-next`), nodejs + npm (frontend build on the hub).
- [x] Same list for a Debian desktop used for development (adds `uv`, mpv windowed context).
- [x] `install.sh` apt list matches the README table exactly.
- **Accept:** a fresh Debian 13 minimal box gets a working show by following only the README.

## Risks

- Shader warp blurs at strong keystone; prefer optical keystone first, note in the UI hint.
- `video-crop` and rotation interplay; test matrix in `test_geometry.py`.

## Docs to update

`features.md` (`/video`, patterns), `architecture.md` (DisplayProfile, geometry module, shader,
routes, WS `display`), `operations.md` (projector setup how-to), `decisions.md` (shader vs vf, precedence).
