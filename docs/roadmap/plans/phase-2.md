# Phase 2 implementation plan: timeline editing quality of life

Spec: `docs/roadmap/phase-2-timeline-qol.md` (binding). Rules: `AGENTS.md` (binding).
Branch `roadmap/phase-2` from master 883487c. Baseline: 165 backend tests, ruff/tsc/oxlint clean.

## Global constraints

- Python 3.13, FastAPI, SQLModel; schema change ⇒ new Alembic revision (current head `b7c1e5a90d42`; chain each new revision on the latest head — check `uv run alembic -c alembic.ini heads` in `backend/`).
- New npm dev dependency allowed: `vitest` only (pick a version compatible with the installed vite; if none, use Node's built-in `node:test` via `tsx`-free plain TS compiled by `tsc` — record the choice). No new runtime npm deps. No new Python deps (no numpy).
- Timeline editor is desktop-first; mobile stays read-only. Keep keyboard shortcuts documented in `docs/features.md` (Task 13 updates docs).
- Every editing operation goes through the existing undo history (`useHistory` in `frontend/src/timeline/model.ts`) and autosave.
- Cue resolution semantics must stay identical between Python (`backend/spookhub/show/resolve.py`) and TS.
- Checks before commit: `cd backend && uv run pytest -q && uv run ruff check . && uv run ruff format --check .`; `cd frontend && npx tsc -b && npm run lint` (no warnings) and `npm test` once it exists.
- Commits: conventional, ending `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. `git add` only files you changed (other agents may work in parallel on other files).
- Don't edit docs except Task 13.

### Task 1: Shared resolver fixtures and TS port

Files: new `backend/tests/fixtures/resolve/*.json`, new `backend/tests/test_resolve_fixtures.py`, new `frontend/src/timeline/resolve.ts`, new `frontend/src/timeline/resolve.test.ts`, `frontend/package.json` (+ lock), maybe `frontend/vite.config.ts`/`tsconfig` for test types.

- Fixture format: `{name, macs, base, cues: [{id, start, duration, targets (null=all | [macs]), state, transition_ms}], probes: [{mac, t, expect_cue (id|null), expect_state}]}`. At least 10 fixtures covering: overlap latest-start wins, duration end resumes longer cue, open-ended superseded, all vs board cue, base when nothing active, transition override, seek-like random probes, equal starts (id tiebreak), cue starting exactly at probe t, end boundary exclusive.
- Pytest runs every fixture through `Timeline.state_at`. TS `resolve.ts` mirrors `Timeline` (`active`, `stateAt`, `overlaps`) and `LightState.merged` semantics (read `models.py` merged). `npm test` runs the same fixtures (import JSON from `../../../backend/tests/fixtures/resolve`).
- Add `"test"` script to package.json.

### Task 2: Lane preview, resolved ribbon, lane abstraction

Files: `frontend/src/timeline/model.ts`, `frontend/src/timeline/TimelineView.tsx`, `frontend/src/pages/TimelineEditor.tsx`, maybe new `frontend/src/timeline/Ribbon.tsx`.

- `Lane` becomes `{kind: 'all' | 'board', id, label, status?}` (phase 4 adds `sound`, phase 5 `device`). All existing code paths keep working.
- Per board lane header: color swatch + effect name (from `board.effects[fx]`) of the resolved state at the playhead (uses `resolve.ts` with profile base + per-board overrides), updates while scrubbing/playing.
- Resolved ribbon: thin strip under each board lane showing the resolved color over time (sample every N px; off ⇒ dark; effect cues hatched or labeled). Recompute memoized on doc/zoom change only.

### Task 3: Cue label/color, profile markers (backend)

Files: `backend/spookhub/models.py`, new migration, `backend/spookhub/api/routes_profiles.py`, `backend/spookhub/show/engine.py` only if cue loading needs the new fields ignored, tests.

- `Cue.label: str | None`, `Cue.color: str | None` (hex `#rrggbb`, validated) in table + `CueIn`.
- `Profile.markers` JSON column: list of `{id: str, t: float ≥0, label: str = "", color: str | None}`; in `ProfileIn/Out`; `PUT /api/profiles/{pid}/markers` replaces the list (publish `profiles_changed`); included in export/import.
- Migration adds the 3 columns with defaults (markers `'[]'`). Tests: round trip, validation, export/import keeps markers and labels.

### Task 4: Markers and cue labels UI

Files: `frontend/src/pages/TimelineEditor.tsx`, `frontend/src/timeline/TimelineView.tsx`, `frontend/src/timeline/CuePanel.tsx`, `frontend/src/types.ts`, `frontend/src/api.ts`, maybe new `frontend/src/timeline/Markers.tsx`.

- `M` drops a marker at the playhead (also while playing). Markers lane above the waveform: draggable, double-click to rename, Delete removes selected marker. Markers are snap targets. Autosaved (debounced like cues) via PUT markers; part of undo history (history state includes markers).
- "Markers → cues": select markers (shift-click / box), choose a template (built-in list), creates one cue per marker on the "All" lane (or the selected lane).
- CuePanel: label text + color picker; cue blocks show label and use the custom color.

### Task 5: Onset and beat analysis (backend)

Files: new `backend/spookhub/media/analyze.py`, `backend/spookhub/media/derive.py` (hook), `backend/spookhub/api/routes_media.py`, tests.

- Pure Python on ffmpeg PCM (reuse the waveform decode at a fixed rate, e.g. 8 kHz mono): RMS envelope at 100 Hz; onset strength = positive diff of log-energy; peak picking with adaptive threshold (moving median + k·MAD) and min spacing 80 ms. Each onset `{t, strength (0..1 normalized)}`. Optional BPM: autocorrelation of onset strength over 0.3–1.5 s lags ⇒ `bpm` or null when weak.
- Stored as `<id>.onsets.json` `{rate, onsets: [...], bpm}`; generated in `derive.ensure` and `GET /api/media/{mid}/onsets` generates on demand for old media (409 while generating is fine, or await). Runs in `asyncio.to_thread`.
- Tests: synthetic signal (silence + 5 clicks at known times, generated in-test with ffmpeg or direct PCM) ⇒ 5 onsets within ±20 ms; click train at 120 BPM ⇒ bpm 120±2.

### Task 6: Onset UI and generate cues

Files: `frontend/src/pages/TimelineEditor.tsx`, `frontend/src/timeline/Strip.tsx` (waveform), `frontend/src/types.ts`, `frontend/src/api.ts`, maybe new `frontend/src/timeline/GenerateDialog.tsx`.

- Onset ticks drawn on the waveform; threshold slider (strength) filters visible onsets; visible onsets are snap targets (toggle).
- "Generate cues" dialog: range (selection or whole video / A–B loop if Task 7 landed), threshold, template, target lane ⇒ one cue per onset. One undo step.

### Task 7: Time editing

Files: `frontend/src/pages/TimelineEditor.tsx`, `frontend/src/timeline/TimelineView.tsx`, `frontend/src/timeline/model.ts`, `frontend/src/timeline/CuePanel.tsx`, maybe new `frontend/src/timeline/Minimap.tsx`.

- Ripple shift: shift all cues (and markers) after the playhead — or the selection — by ±Δ s (dialog; keyboard: none). Insert time at playhead; delete range (A–B) removing cues fully inside and shifting later ones.
- Loop region A–B: `[` sets A, `]` sets B at playhead, `\` clears; preview video loops A–B; audition follows.
- CuePanel numeric start/duration fields with ±frame and ±0.1 s buttons.
- Zoom to fit (`F`), zoom to selection (`Shift+F`), minimap/overview bar above lanes (click/drag to scroll), timecode ruler `mm:ss.ff` with frame numbers at high zoom (fps from media probe if available, else 25).

### Task 8: Backend for templates, effect metadata, audition mute

Files: `backend/spookhub/models.py`, new migration, new `backend/spookhub/api/routes_templates.py`, `backend/spookhub/main.py`, `backend/spookhub/boards/client.py`, `backend/spookhub/boards/registry.py`, `backend/spookhub/api/routes_show.py` (audition), `backend/spookhub/show/engine.py` (audition), tests.

- `CueTemplate` table: id, name, cues JSON (list of `{offset_s, duration_s, lane: 'all'|'first'|'same', state, transition_ms, label, color}`), created_at. `GET/POST/DELETE /api/templates`. Lock middleware blocks POST/DELETE automatically.
- Board effect metadata: in `refresh_meta` also fetch `/json/fxdata` (list of strings, same index as effects; tolerate 404 ⇒ empty) and store as `fxdata` on BoardRuntime + DB (`Board.fxdata` JSON column, same migration). Board snapshot exposes `fxdata`. Simulator serves `/json/fxdata`.
- Audition mute: `AuditionIn.muted: list[str] = []` (macs). Muted boards get the profile base during audition instead of cue states.
- Tests for each.

### Task 9: Clipboard, saved templates, parametric templates

Files: `frontend/src/timeline/templates.ts`, `frontend/src/pages/TimelineEditor.tsx`, `frontend/src/api.ts`, `frontend/src/types.ts`, new `frontend/src/timeline/TemplateDialog.tsx`.

- Ctrl+C/Ctrl+V: copy selected cues to `localStorage` key `spookhub.clipboard` as `{v:1, cues:[relative times, lane ids]}`; paste at playhead in any profile; when lane macs don't exist in this profile, show a remap dialog (map each source lane to a target lane or drop).
- "Save selection as template" (name prompt) ⇒ POST /api/templates; saved templates listed with built-ins; insert at playhead respects relative timing.
- Parametric built-ins as generator functions `(params, ctx) => cues[]`: heartbeat (bpm, duration, color), breathe (period, duration, color, min/max bri), fire (duration, intensity), police (duration, rate), strobe burst (count, rate, color), creep (duration, from bri, to bri, color), lightning (duration, flashes, seed). Insert dialog with param form and live preview drawn on the ribbon (Task 2) before committing. One undo step.

### Task 10: Effect picker, mute/solo, panel undo

Files: `frontend/src/timeline/CuePanel.tsx`, `frontend/src/components/LightStateEditor.tsx`, new `frontend/src/components/EffectPicker.tsx`, `frontend/src/timeline/TimelineView.tsx` (lane mute/solo buttons), `frontend/src/pages/TimelineEditor.tsx` (audition muted list).

- Searchable effect picker (popover list with filter), favorites (star) and recently used, stored in `localStorage`; palette picker the same.
- When the board has `fxdata` for the effect, show only the sliders it uses with their labels (WLED fxdata format: `"name@sx label,ix label,...;colors;palette;flags"` — parse the slider part; `!` = default label; missing = hidden).
- Lane mute/solo toggles (lane header); audition sends `muted` macs.
- Every CuePanel edit is one undo step (coalesce continuous slider drags into one step).

### Task 11: WLED segment targeting (backend)

Files: `backend/spookhub/models.py`, migration, `backend/spookhub/boards/registry.py`, `backend/spookhub/boards/client.py` (build_payload), `backend/spookhub/show/resolve.py`, `backend/spookhub/show/engine.py`, `simulator/wled_sim.py`, tests + fixtures update.

- Board stores `segments: [{id, name, start, stop}]` from `/json/state` `seg` on refresh (DB JSON column).
- `CueTarget.segs: dict[mac, list[int]] | None` — absent = whole board (old cues unchanged).
- Resolver: per board, cues targeting segments resolve per (mac, seg); the scheduler builds one payload per board: whole-board state plus `seg: [{id, ...state}]` entries for segments with their own active cue; the latest-wins queue stays per board.
- Fixtures (Task 1 format extended with `seg` in probes) cover segment overlap; TS port updated in Task 12.
- Tests: two segments with different cues on one board in sync (simulator receives one request containing both seg entries).

### Task 12: Segment UI

Files: `frontend/src/timeline/resolve.ts` (+ test), `frontend/src/timeline/TimelineView.tsx`, `frontend/src/timeline/model.ts`, `frontend/src/timeline/CuePanel.tsx`, `frontend/src/types.ts`.

- Expand a board lane into segment sub-lanes (toggle in lane header) when the board has >1 segment; cues on sub-lanes target that segment. CuePanel target picker shows segments. Resolver TS port handles segments (fixtures from Task 11 pass in `npm test`).

### Task 13: Docs and roadmap

Update `docs/features.md` (keys, markers, templates, picker, segments, time editing), `docs/architecture.md` (markers, CueTemplate, fxdata, segments, onsets file, label/color, audition muted, new routes), `docs/development.md` (npm test / fixtures), `docs/decisions.md` (markers as JSON column, no numpy energy onsets, clipboard format, vitest choice, segment payload merge), roadmap phase-2 checkboxes + README status.
