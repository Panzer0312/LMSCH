# Phase 4: Scenes, audio-only cues, ambient layer

**Goal:** repeat visitors get variety, sound effects sit on the timeline next to lights, the idle
state feels alive instead of waiting, and one-shot "moments" are one button press away.

## 4.1 Scenes (playlists)

- [ ] New profile type `scene`: `items` [{profile_id, weight}], `order` sequential | shuffle-no-repeat | weighted,
  idle profile (video or light), cooldown. Migration.
- [ ] Engine scene runner: idle part runs; trigger picks next item, plays it, returns to idle without
  re-applying idle from scratch. Dashboard shows "next up" and allows skip/pin next.
- [ ] Optional time-based item: chain items back to back (playlist mode, no trigger).
- [ ] Scene editor in Profiles: drag order, weights, preview "next 10 picks".
- **Accept:** 3 scares in shuffle mode never repeat back to back over 30 triggers (test with seeded RNG).

## 4.2 Audio-only cues

- [ ] Media accepts audio files (`Media.kind` video | audio). Probe, waveform; no thumbnails.
- [ ] **SPIKE** second audio stream: a second supervised `MpvPlayer` instance (`--no-video`, own
  socket) for sound effects. Two ALSA openers need `dmix`; `install.sh` writes an ALSA config with a
  dmix default on the chosen device. Verify latency and that main video audio is unaffected.
  Fallback if dmix fails: mix effects into main mpv via `af`/`lavfi` (note result in decisions).
- [ ] Sound lane in the timeline (uses 2.1 lane abstraction): sound cue = media, start, volume, fade in/out, loop?
- [ ] Scheduler fires sound cues off main `time-pos` with the same latency path; seek recomputes
  (a sound whose window covers the seek point starts at the right offset).
- [ ] Standalone sound buttons ("soundboard") on `/show` for manual scares.
- **Accept:** door creak cue within ±50 ms of its timestamp on real hardware; main audio keeps playing.

## 4.3 Ambient layer

- [ ] Idle track: a video profile's idle video gets its own cue timeline (`Cue.track` main | idle,
  migration). It loops with the idle video. Timeline editor tab "Idle".
- [ ] Ambient sound loop per profile (light, video or scene), played on the effects player, ducked
  during the main video (volume ramp).
- [ ] Light profiles can loop an idle timeline too (fixed length, no video) for breathing lights.
- [ ] Transition idle → main: fade out (`brightness` + `volume` ramp over N ms), load, fade in. Lights
  use the cue transition. Configurable per profile, default 400 ms. **SPIKE** ramp smoothness via IPC.
- **Accept:** idle fog loop with breathing lights and wind sound; trigger fades into the scare; after it ends, ambient resumes.

## 4.5 Moments page (`/moments`)

A button board for desktop and phone. Each button plays a preselected **moment** exactly once, then
returns to whatever ran before. Example: a guest rings the door, you press "Doorbell": spooky organ
music, flickering lights and a video of Dracula welcoming the guest to the haunted house. The next
press picks a different variant.

**Model**
- [ ] `Moment` table + migration: name, emoji/icon, color, sort, cooldown_s, busy policy
  (`ignore` | `interrupt` | `queue` when something else is playing), `variants` JSON list.
- [ ] Variant = any combination of: video profile (plays once with its cues, whatever its own mode),
  sound media (via the 4.2 effects player, volume, fade), light look (light profile, or a fixed-length
  light timeline from 4.3 for audio-only moments), ambience override for the duration
  (ambient sound muted/replaced, base lights changed), weight.
- [ ] Variant pick: shuffle-no-repeat by default, weighted optional (reuses the 4.1 picker).

**Engine**
- [ ] `ShowEngine.play_moment(id)`: snapshot the current state (active profile, idle/ambient
  position, manual overrides), fade out ambient (4.3 fade), run the variant once, then restore the
  snapshot (back into idle/ambient, without restarting the profile from scratch).
- [ ] Respects blackout (refused), cooldown, busy policy. Moments run under the show-mode lock (1.5): pressing allowed, editing not.
- [ ] API: CRUD `/api/moments`, `POST /api/moments/{id}/play`, `POST /api/moments/stop`. WS `moment`
  (playing id, variant, progress, cooldown remaining).

**UI**
- [ ] `/moments` page: grid of big buttons (2 columns at 375 px, 4–6 on desktop), emoji + name +
  color, live progress ring while playing, cooldown ring after, "Stop" and Blackout always visible.
  Optional fullscreen kiosk view for a tablet by the door.
- [ ] Moment editor (from the page, hidden in show mode): add/remove variants, pick video, sound and
  light parts, "test this variant" button, reorder buttons by drag.
- [ ] Link from Dashboard and `/show`.

- **Accept:** "Doorbell" with two variants: press 1 plays Dracula video + organ + flicker once, then
  the fog idle + wind ambience resume where they were; press 2 plays the other variant; pressing
  during cooldown shows the remaining seconds and does nothing.
- Later (phase 5, optional): a named trigger can fire a moment, so a real doorbell button needs no phone.

## Risks

- ALSA dmix on HDMI is the main unknown; spike before building the sound lane.
- Scene runner touches engine state machine; extend `test_e2e.py` with a scene cycle.
- Moment restore must not glitch the idle loop; e2e test: idle → moment → idle, boards back to idle state.

## Docs to update

`architecture.md` (scene type, second player, Cue.track, Media.kind, Moment), `features.md` (incl. `/moments`), `operations.md`
(ALSA dmix), `decisions.md` (scene as profile type, mixing approach, moment restore semantics).
