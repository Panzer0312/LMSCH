# Phase 4 implementation plan: scenes, audio cues, ambient, moments

Spec: `docs/roadmap/phase-4-scenes-audio-ambient.md` (binding). Rules: `AGENTS.md` (binding).
Branch `roadmap/phase-4` from master e21c9e1. Baseline: 316 backend tests, 57 vitest.

## Global constraints

- No new Python/npm deps. New apt packages ⇒ `install.sh` + README table.
- Engine owns mpv players during a show. Several agents edit `show/engine.py` in parallel: make small,
  targeted edits, re-read the region right before editing, never rewrite large blocks.
- Fixed Alembic revision ids (create exactly these, chained as listed, so parallel work doesn't fork):
  `b1a0d4e5c601` (audio, down `a9c4e7d2b153`) → `b2c3e4f5a602` (scenes) → `b3d4f5a6b703` (ambient/idle track) → `b4e5a6b7c804` (moments).
  If yours is later in the chain and the earlier one doesn't exist yet, still use that down_revision; tests run after the wave lands.
- UI never polls; WS via `hub.bus.publish`. Pages work at 375 px.
- Each agent: explicit `git add` of own files; conventional commit + `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. Don't edit docs (final task does).

## Shared contract

### Media (`b1a0d4e5c601`)
- `Media.kind: 'video' | 'audio'` (default video). Upload accepts audio (mp3, wav, ogg, flac, m4a, opus): probe, waveform, onsets; no thumbs/transcode. List items include `kind`.

### Effects players (`player/effects.py`)
- Two extra supervised mpv instances (`--no-video`, own IPC sockets): `fx` (sound cues, soundboard, moment sounds) and `ambient` (ambient loop). Reuse `MpvPlayer` (subclass/flag) — same audio device + volume caps as main.
- `fx.play(path, start=0.0, volume=1.0, fade_in_ms=0, loop=False) -> None`; `fx.stop(fade_out_ms=0)`. One sound at a time per player; a new play replaces the old.
- Volume ramps (fade/duck) via repeated `set volume` steps (~20 ms) in an asyncio task.
- `install.sh` writes `/etc/asound.conf` with a `dmix` default on the configured ALSA card when absent (idempotent, keep a user file untouched); dev desktop (PipeWire) needs nothing.

### Sound cues (`b1a0d4e5c601`)
- `Cue.kind: 'light' | 'sound'` (default light), `Cue.sound: {media_id, volume (0..1.3, default 1), fade_in_ms, fade_out_ms, loop} | null`. Sound cues ignore `target`/`state`. `CueIn` mirrors it.
- Timeline lane `{kind: 'sound', id: '__sound__', label: 'Sound'}`. Scheduler fires sound cues off main `time-pos` like light cues; seek starts a sound whose window covers t at offset `t - start`. Sound cue with duration stops (fade_out) at its end; without duration plays to its own end.
- Soundboard: `POST /api/sounds/play {media_id, volume?}`, `POST /api/sounds/stop`. Allowed under show lock.

### Scenes (`b2c3e4f5a602`)
- `ProfileType.scene`. `Profile.scene: {items: [{profile_id, weight (1)}], order: 'sequential'|'shuffle'|'weighted', idle_profile_id: int|null, chain: bool, light_hold_s: 10}` JSON, null for other types. In ProfileIn/Out.
- `show/picker.py`: `Picker(order, rng)` with `next(items, history) -> id`; shuffle = shuffle-no-repeat (never same as last; reshuffle bag), weighted = weighted random without immediate repeat when >1 item. Seeded RNG injectable. Reused by moments.
- Engine: scene active ⇒ idle part = idle profile (video idle loop or light base). Trigger ⇒ play next item (video profile once with its cues; light profile = its base for `light_hold_s`), then back to idle without re-activating from scratch. `chain` ⇒ items back to back without trigger.
- API: `GET /api/scene` ⇒ `{profile_id, next_id, pinned, history: [ids], upcoming: [10 ids preview]}`; `POST /api/scene/skip`; `POST /api/scene/pin {profile_id|null}`. WS `scene` with the same payload on change.

### Ambient (`b3d4f5a6b703`) — wave 2
- `Cue.track: 'main' | 'idle'` (default main). Idle track loops with the idle video (or `Profile.idle_length_s` for light profiles).
- `Profile.ambient: {media_id, volume, duck_to (0.3)} | null`; `Profile.fade_ms` default 400 (idle → main fade).

### Moments (`b4e5a6b7c804`) — wave 2
- `Moment` table: name, icon, color, sort, cooldown_s, busy ('ignore'|'interrupt'|'queue'), variants JSON
  `[{video_profile_id?, sound?: {media_id, volume, fade_in_ms, fade_out_ms}, light_profile_id?, ambience: 'keep'|'mute'|'replace', ambience_media_id?, weight}]`, order 'shuffle'|'weighted'.
- API `/api/moments` CRUD, `POST /{id}/play`, `POST /stop`; WS `moment` `{playing_id, variant, progress, cooldown: {id: seconds}}`.

## Waves

1. A audio+effects+sound cues (backend) · B scenes (backend) · C1 frontend scenes/dashboard/media audio · C2 frontend sound lane/soundboard.
2. D ambient + idle track (backend+frontend) · E moments (backend+frontend).
3. Review, fixes, docs, roadmap.
