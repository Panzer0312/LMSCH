# Phase 4: Scenes, audio-only cues, ambient layer

**Goal:** repeat visitors get variety, sound effects sit on the timeline next to lights, and the idle
state feels alive instead of waiting.

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

## Risks

- ALSA dmix on HDMI is the main unknown; spike before building the sound lane.
- Scene runner touches engine state machine; extend `test_e2e.py` with a scene cycle.

## Docs to update

`architecture.md` (scene type, second player, Cue.track, Media.kind), `features.md`, `operations.md`
(ALSA dmix), `decisions.md` (scene as profile type, mixing approach).
