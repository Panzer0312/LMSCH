# Phase 6: Board groups (rooms)

> **Built 2026-10-31, hardware untested.** Overlap rule deviates from the design below, see `docs/decisions.md`.

Named groups of boards, e.g. "Dining room" (3 boards), "Hall" (1 board). Groups can be picked
anywhere the app selects boards today, so a profile, cue or moment can drive one room, or two
groups in different looks (2 boards yellow, 2 red).

Today: `BoardSelection {all, macs}` (profile boards), `CueTarget {all, macs, segs}` (cue target),
`Profile.overrides` keyed by MAC, timeline lanes per board and segment.

## Design rules

- Groups are resolved to MACs **at apply time**, not copied on save. Adding a board to a group
  updates every profile, cue and moment that uses it.
- A board may be in several groups ("Dining room" and "Window side"). Overlaps resolve like
  overlapping cues today: the more specific target wins (segment > board > group > all), and the
  later cue wins at equal level. **Built instead:** latest start wins at every level (see decisions).
- Deleting a group that is in use is refused with a "used by" list, like media.
- Unknown or empty group ⇒ preflight warning, the cue does nothing (no crash).

## 6.1 Model and API

- [x] `BoardGroup` table: id, name, color, order; `board_group_member` (group_id, mac). Alembic revision.
- [x] `BoardSelection.groups: list[int]` and `CueTarget.groups: list[int]`, default `[]`,
  backwards compatible. One resolver `resolve_macs(selection | target)` used by the engine,
  moments, scenes and fallbacks.
- [x] `Profile.overrides` accepts `group:<id>` keys next to MACs (board key wins over group key).
- [x] REST: `GET/POST/PUT/DELETE /api/groups`, `PUT /api/groups/order`; WS `groups_changed`.
- [x] Export/import and backup carry groups; import maps group ids by name.
- **Accept:** a cue targeting "Dining room" drives exactly its 3 boards; moving a board into the
  group changes the live result without editing the cue. Tests for resolver and overlap order.

## 6.2 Boards page

- [x] Group manager: create, rename, color, reorder, delete (with "used by"). Works at 375 px.
- [x] Per board: group chips, assign by tap. Filter the board list by group.
- [x] Group actions: on/off, brightness, identify (flash all boards of the group).

## 6.3 Profiles, scenes, moments

- [x] Board picker everywhere (profile form, quick cue list, timeline cue panel). Scene and moment
  editors have no board target: they reuse profiles (decision row). Order: "All boards", groups as
  chips, then single boards.
- [x] Per-group base overrides in the profile form ("Dining room: yellow, Hall: red").
- **Accept:** a light profile with "Dining room" yellow and "Hall" red shows both on the boards.

## 6.4 Timeline editor

- [x] Group lanes above board lanes (collapsible, show member boards). Cues on a group lane target
  the group. Mute/solo and live audition work per group.
- [x] Generators and templates can target a group lane; "Remap" can swap group ↔ group.
- [x] Overlap highlighting accounts for group membership.

## 6.5 Show page and dashboard

- [x] Board panel on `/show` grouped by room, with per-group Auto / Off / White.
- [x] Dashboard board health grouped by room.

## Docs

`docs/architecture.md` (model, resolver), `docs/api.md` (routes, WS), `docs/features.md`,
`docs/timeline.md`, a `docs/decisions.md` row (resolve at apply time; overlap order).
