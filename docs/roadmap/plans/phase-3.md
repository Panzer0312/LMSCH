# Phase 3 implementation plan: video setup page

Spec: `docs/roadmap/phase-3-video-setup.md` (binding). Rules: `AGENTS.md` (binding).
Branch `roadmap/phase-3` from master 3589144. Baseline: 223 backend tests, 51 vitest, ruff/tsc/oxlint clean.
Dev mpv 0.41 has every property in the spec table (`--list-options` checked). Hub spikes stay open.

## Global constraints

- Python 3.13, FastAPI, SQLModel; schema change ⇒ new Alembic revision chained on the current head
  (`uv run alembic -c alembic.ini heads` in `backend/`). No new Python or npm deps.
- mpv only through `MpvPlayer`; the engine owns mpv during a show. Setup mode refuses (409) while blackout,
  stops any show on entry, restores the active profile idle state on exit (same pattern as `set_test_pattern`).
- UI never polls: state goes out via `hub.bus.publish("display", ...)`.
- Every agent: `git add` only its own files; commit message conventional + `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
  Don't edit docs (Task 6 does).

## Shared contract (all agents code against this)

### `DisplayGeometry` (Pydantic, `backend/spookhub/models.py`; TS mirror in `frontend/src/types.ts`)

| Field | Type / range | Default | mpv |
|---|---|---|---|
| `zoom` | float −2..2 (log2) | 0 | `video-zoom` |
| `pan_x`, `pan_y` | float −1..1 | 0 | `video-pan-x/y` |
| `scale_x`, `scale_y` | float 0.25..4 | 1 | `video-scale-x/y` |
| `align_x`, `align_y` | float −1..1 | 0 | `video-align-x/y` |
| `rotate` | 0 \| 90 \| 180 \| 270 | 0 | `video-rotate` |
| `aspect` | float 0.2..10 \| null | null | `video-aspect-override` (null ⇒ -1 = "no override") |
| `crop` | `{x, y, w, h}` fractions 0..1 of source | `{0,0,1,1}` | `video-crop` `WxH+X+Y` px (needs source size; full ⇒ `""`) |
| `brightness`, `contrast`, `gamma`, `saturation` | int −100..100 | 0 | same names |
| `corners` | 4 × `[dx, dy]`, order TL, TR, BR, BL, each −0.5..0.5 of output size | zeros | shader |
| `mask` | `{top, right, bottom, left, feather}` 0..0.5 | zeros | shader |
| `flip_h`, `flip_v` | bool | false | shader |

Shader `backend/spookhub/player/shaders/warp.glsl` (packaged as data), params via `//!PARAM`:
`c0x c0y c1x c1y c2x c2y c3x c3y mask_t mask_r mask_b mask_l feather flip_h flip_v` (all float).
mpv started with `--glsl-shaders=<path>` always; identity params ⇒ visually unchanged.

### Data model
- `DisplayProfile` table: id, name (unique), geometry JSON.
- `RuntimeSettings.display_profile_id: int | None` (global default), `Media.display_profile_id`, `Profile.display_profile_id` (nullable FKs-by-id). Precedence profile > media > global > identity.
- Profile export carries `display_profile` as `{name, geometry}`; import matches by name or creates it.

### REST (`backend/spookhub/api/routes_display.py`, prefix `/api/display`)
| Method | Path | Body / result |
|---|---|---|
| GET | `/profiles` | `DisplayProfileOut[] {id, name, geometry}` |
| POST | `/profiles` | `{name, geometry}` ⇒ DisplayProfileOut |
| PUT | `/profiles/{id}` | `{name?, geometry?}` ⇒ DisplayProfileOut |
| DELETE | `/profiles/{id}` | clears references to it |
| GET | `/state` | `DisplayState` (same as WS payload) |
| POST | `/setup` | `{on: bool, source?: Source}` enter/leave/switch source. 409 blackout. |
| POST | `/live` | `{geometry}` apply live without saving (debounced in player, ≤30 ms) |
| POST | `/seek` | `{t}` scrub media source |
| GET | `/patterns` | `[{id, label, kind: 'image'\|'video'}]` |

`Source` = `{kind: 'pattern', id: str} | {kind: 'media', id: int} | {kind: 'idle'}`.
`DisplayState` (WS type `display`) = `{setup: bool, source: Source | null, live: DisplayGeometry | null, resolved_profile_id: int | null, geometry: DisplayGeometry}` where `geometry` is what mpv currently shows.
Setting `media.display_profile_id`: `PATCH /api/media/{id}` `{display_profile_id}`. Global default via existing `PUT /api/settings`. Profile via existing profile PUT.
Old `POST /api/show/test_pattern` keeps working (grid pattern through geometry).

### Patterns (`backend/spookhub/show/patterns.py`)
`PATTERNS: list[Pattern(id, label, kind)]`; `async def ensure(data_dir, id, size=(1920,1080)) -> Path`.
Ids: `grid, corners, circles, edge_chase, scan_h, scan_v, focus, grey_ramp, checker, white, black, safe_area`.
Images PNG, moving ones (`edge_chase, scan_h, scan_v`) 4–6 s looped H.264 mp4. `safe_area` = grid-free frame
with mask outline drawn by the shader itself (render a mid-grey frame; the mask shows its edges).
`testpattern.ensure_pattern` becomes a thin wrapper over `ensure(data_dir, 'grid')`.

### Media helpers
- `GET /api/media/{id}/black_check` ⇒ `{ok: bool, black_level: float (0..1 median of sampled YLOW/255), sampled: int, message}`; ffmpeg `signalstats` on ~1 fps sample. Cached in `<id>.black.json`.
- Media list items gain `aspect` (w/h after rotation) and `display_profile_id`.

## Tasks

1. **Backend core** (agent A): models + migration, `player/geometry.py` (pure, unit tests incl. identity ⇒ mpv defaults, crop/rotate matrix), shader, `MpvPlayer.apply_geometry` (diff-only, debounce, reapply after restart and after every `load()`), engine resolution + setup mode, `routes_display.py` (except `/patterns`), WS `display`, export/import, e2e read-back with `vo=null`.
2. **Patterns + media helpers** (agent B): `show/patterns.py`, `/api/display/patterns` route (own small router file `routes_patterns.py` mounted at the same prefix), black check, media `aspect`, `PATCH /api/media/{id}`, tests.
3. **Frontend `/video`** (agent C): page, keystone editor, WebGL preview, profile bar, A/B, phone layout, nav link; pattern picker on `/setup`; Media page aspect badge + "create display profile" + black check.
4. **README + install.sh** packages (builder).
5. Review + fix.
6. Docs + roadmap ticks.
