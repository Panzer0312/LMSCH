# Phase 7: Quality of life

> **TODO. Not started.** Collected 2026-10-31 after phase 6. Start only on an explicit go.
> Ordered by value for a show night versus effort.

## 7.1 Show night

- [ ] **Moment by external trigger.** `POST /api/moments/{id}/play` with the trigger token, so a
  doorbell or button box fires a moment without a phone. Reuses the token check of `/api/trigger`.
- [ ] **Offline alarm on `/show`.** A board that drops offline during a show gets a red banner and a
  short vibration (`navigator.vibrate`) on the operator phone, cleared when it returns.
- [ ] **Room quick looks on `/show`.** Per room: a colour row (red, orange, purple, white, off) next to
  Auto / Off / White. Manual override, cleared by Auto.
- [ ] **Rehearsal switch.** One toggle: caps to 50 % / 60, triggers count separately in History.
  Today the Rehearsal / Show night test presets cover the caps part.
- [ ] **Scare stats.** History lists plays per scene item and moment tonight, so a dud scare is easy
  to spot.

## 7.2 Setup

- [ ] **In-app help.** Serve `docs/tutorial/*.md` at `/help` (rendered on the hub), linked from the
  nav and the Settings test presets, so the tutorial is readable on a phone at the door.
- [ ] **Save live look as profile.** Capture every adopted board's live WLED state into a new light
  profile (per-board overrides), after tweaking in the WLED UI.
- [ ] **Firmware check in preflight.** Warn when boards run different WLED versions or one older than
  0.14 (effect ids and fxdata differ).
- [ ] **Latency history.** Per-board latency sparkline (last hour) on Boards, from the health loop.
- [ ] **Test presets from real rooms.** "Two rooms" uses existing rooms when there are at least two,
  instead of creating `[Test] Room A/B`.

## 7.3 Editing

- [ ] **Timeline room lanes collapsible.** ▸ on a room lane shows its member boards indented, like
  segment sub-lanes; drag a cue from a board lane to a room lane retargets it.
- [ ] **Show bundle export.** One zip: all profiles, rooms, moments, templates and a media file list
  (not the media), for moving to a new hub or a second house.
- [ ] **Profile form undo.** "Revert to saved" on the profile form, like the timeline undo.
- [ ] **Room colour in cue blocks.** Cues on a room lane get a thin stripe in the room colour.

## Not planned

- Group CRUD refusing while a show plays: profile edits reload live too, and an operator fixing a
  room mid-show is the use case. Show lock is the guard.
