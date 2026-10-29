# Phase 2: Timeline editing quality of life

**Goal:** building a 3-minute show takes minutes, not an hour. The editor shows what the boards
will do without boards present.

## 2.1 Shared resolver and live lane preview (foundation)

- [x] Port `show/resolve.py` to `frontend/src/timeline/resolve.ts`. Shared JSON fixtures in
  `backend/tests/fixtures/resolve/*.json`, run by pytest and by a new vitest suite (`npm test`).
- [x] Lane preview: per lane a color swatch + effect name at the playhead, updated while scrubbing.
- [x] Resolved-state ribbon under each lane: the color the board will show over time, so gaps and
  overrides are visible at a glance.
- [x] Generalize lanes to `{kind: 'board' | 'all', id}` so phase 4 can add sound and device lanes.
- **Accept:** fixtures give identical results in Python and TS; ribbon matches audition on real boards.

## 2.2 Markers

- [x] `Marker` table (profile_id, t, label, color) + migration, part of export/import.
- [x] `M` drops a marker at playhead, also while playing (tap along to the video). Markers lane
  above the waveform, draggable, renameable.
- [x] Markers are snap targets. "Markers → cues": select markers, pick template, one cue each.
- **Accept:** tap 10 markers during playback, convert to 10 cues in two clicks.

## 2.3 Onset and beat detection

- [x] `media/analyze.py`: from ffmpeg PCM (already used for waveform) compute RMS at 100 Hz and
  spectral-flux onsets with numpy-free code or numpy (decide, note in decisions). Optional BPM via
  autocorrelation. Stored as `<id>.onsets.json`, built on upload and on demand for old media.
- [x] Onset ticks on the waveform with a threshold slider. Snap to onsets.
- [x] "Generate cues": onsets above threshold in a range ⇒ cues from a template (e.g. lightning on thunder).
- **Accept:** a thunder clip yields one onset per strike with default threshold.

## 2.4 Time editing

- [x] Ripple shift: move all cues after playhead (or selection) by ±Δ s. Insert time, delete range.
- [x] Loop region A–B for preview and audition (`[` and `]` keys).
- [x] Numeric nudge in cue panel (start, duration) with frame step buttons.
- [x] Zoom to fit, zoom to selection, minimap/overview bar, timecode ruler with frame numbers.
- **Accept:** after trimming 2 s from the video start, one ripple shift realigns all cues.

## 2.5 Clipboard and reuse

- [x] Copy/paste cues (Ctrl+C/V) via `localStorage`, across profiles; paste at playhead keeps
  relative timing. Lane remap dialog when target boards differ.
- [x] `CueTemplate` table: "Save selection as template" (multi-cue, relative times). Shown with built-ins.
- **Accept:** a 5-cue lightning burst saved once, pasted into another profile at a new time.

## 2.6 Parametric templates

- [x] Template = function of params (duration, color, rate, intensity, count). Built-ins:
  heartbeat, breathe, fire, police, strobe burst, creep (dim → bright ramp), lightning (random seeded).
- [x] Insert dialog with param form and preview on the ribbon before committing.
- **Accept:** heartbeat at 80 bpm for 10 s produces correctly spaced pulse cues.

## 2.7 Effect and palette picker

- [x] Searchable picker with favorites and recently used (localStorage).
- [x] Read WLED `/json/fxdata` per board; show only the sliders an effect uses, with their labels.
- [x] Palette gradient swatches (from `/json/palx` where supported, else name only).
- **Accept:** picking "Fire 2012" shows its real slider names.

## 2.8 Audition helpers

- [x] Lane mute/solo for audition (muted lanes send base state).
- [x] Cue labels and custom colors. Undo covers every panel edit, not only move/add/delete.

## 2.9 WLED segment targeting (largest item, last)

- [x] Board stores segments (id, name, start, stop) from `/json/state` on refresh.
- [x] `CueTarget` gains optional `segs: {mac: [seg ids]}`; lane per (board, segment) when expanded.
- [x] Resolver keys by (mac, seg). Payload builder merges segment states into one `seg: [...]`
  request so the latest-wins queue stays per board.
- [x] Migration keeps old cues as whole-board.
- **Accept:** two segments on one strip run different cues in sync; resolver fixtures cover segment overlap.

## Docs to update

`features.md` (keys, markers, templates), `architecture.md` (Marker, CueTemplate, segments,
onsets file), `development.md` (vitest), `decisions.md` (numpy or not, clipboard format).
