# Timeline editor

Route `/profiles/:id/timeline`. Desktop-first, read-only on phone.

- **Tabs Main / Idle:** Main edits the main video's cues. Idle edits the idle track: cues that loop
  with the idle video, or every "Idle loop length" for light profiles (no video; a stand-in
  playhead loops). Light profiles open on Idle; Main is disabled. Without an idle video or loop
  length the Idle tab says how to get one. Both tracks save together.
- **Lanes:** "All selected" on top, one lane per board in the profile, then the Sound lane. Board lane header shows the
  resolved color + effect at the playhead; a ribbon under the lane shows the resolved color over time.
- **Overview bar** above the lanes: cues, markers, playhead, visible window. Click or drag to scroll.
- **Ruler:** m:ss; zoomed in far enough it switches to `mm:ss.ff` frame ticks (25 fps).
- **Editing:** drag to move, drag edges to resize, double-click empty lane to add. Shift/Ctrl-click
  multi-select. Snap to cue edges, playhead, markers and visible onsets; Alt disables snap. Ctrl + wheel zooms.
- **Keys:**

| Key | Action |
|---|---|
| Space | Play/pause |
| ←/→, Shift+←/→ | Seek 0.1 s / one frame |
| C | Add cue at playhead |
| M | Drop marker at playhead (also while playing) |
| Delete | Delete selected cues and markers |
| Ctrl+D | Duplicate |
| Ctrl+C / Ctrl+V | Copy / paste at playhead (works across profiles) |
| Ctrl+Z / Ctrl+Shift+Z | Undo / redo |
| `[` / `]` / `\` | Loop start / loop end / clear loop |
| F / Shift+F | Zoom to fit / zoom to selection |
| + / − | Zoom |

- **Cue panel:** label and custom block color; numeric start/duration with ±1 frame and ±0.1 s
  buttons; searchable effect and palette picker with favorites and recents. When the board reports
  WLED fxdata, only the sliders the effect uses are shown, with their real names. Every panel edit is
  undoable; a burst of edits to one cue (slider drag, typing) is one undo step.
- **Markers:** lane above the waveform. Drag to move, double-click to rename. Select markers, then
  "Markers → cues" makes one cue per marker from a template. Saved with the profile and in exports.
- **Onsets:** ticks on the waveform from audio analysis. The threshold slider filters them.
  "Generate cues…" makes one cue per onset above the threshold in a range (default: loop A–B).
- **Loop A–B:** preview playback loops the range; audition follows.
- **Time editing:** Δ s field, then "⇤ Shift"/"Shift ⇥" moves everything after the playhead (or only
  the selection), "Insert time" opens a gap at the playhead, "Delete A–B" removes the loop range and
  closes the gap.
- **Paste remap:** when pasted cues target boards missing in this profile, a dialog maps each source
  board to a lane here or drops it.
- **Templates…:** parametric generators (heartbeat, breathe, fire, police, strobe burst, creep,
  lightning) with a parameter form and a live preview on the ribbon. "Save as template" stores the
  selection (relative times) on the hub; saved templates appear in the same dialog.
- **Mute/solo (M/S in the lane header):** muted lanes get the profile base during live audition.
- **WLED segments:** boards with more than one segment get ▸ in the lane header to show one
  sub-lane per segment. Cues added on a sub-lane target only that segment; the cue panel target list
  has per-segment checkboxes. Segment cues on a collapsed board are drawn on the board lane.
  Segments follow on/brightness of the whole board; effect, palette, speed, intensity and colors are per segment.
- **Overlaps** on a board get a warning badge.
- **Live audition:** browser preview drives real boards. Browser reports playhead every 1 s and on
  play/pause/seek; backend interpolates. Tab closes ⇒ boards to base after 3 s. Rejected while a show plays.
- **Capture from board:** pull a board's live WLED state into the selected cue.
- **Play on projector from here:** real show from the playhead.
- **Autosave** 0.8 s after last edit, with indicator. Reload/close with unsaved or failed saves warns; "← Back" asks.
  Undo history is kept per track across Main/Idle tab switches (dropped if markers changed in the other tab).
  Enter in "Loop length" saves it.
- **Templates:** Flicker, Strobe, Blackout, Blood red, Lightning flash. Add more in
  `frontend/src/timeline/templates.ts`.
- **Sound lane:** "+ Sound" (or double-click the lane) adds a sound cue at the playhead from an
  audio file. The bar shows a mini waveform. Panel: sound, label, color, start, duration (empty =
  play to the end), volume, fade in/out (ms), loop. One sound plays at a time: the latest-started
  covering cue wins, an older longer one resumes after it. Seek starts a covering sound at the
  right offset. Sound cues are not saved into templates.
