# Decisions

Choices made where the spec was open. Append new rows; mark replaced rows "superseded by …".

| Topic | Choice |
|---|---|
| Trigger cooldown | Counted from end of previous play. Triggers while playing ignored. |
| Trigger in once/loop mode | Rejected. Use Play/Stop. |
| Blackout release | Re-activates current profile from start. |
| Manual board overrides | Cleared on next profile activation. |
| Health check | Poll `/json/si`; WLED WebSocket unused (one code path, robust). |
| Audio | ALSA directly. PipeWire needs a user session; system service has none. |
| Boards outside new profile's selection | Left as they are on profile switch. |
| Trigger token vs dashboard | Separate endpoints. Token guards only external trigger. |
| Live audition while show plays | Rejected. Projector show has priority. |
| Transcode target | Same media id, so profiles keep working. |
| Lock allowlist | While locked, only show-time calls pass (see architecture). Pin and trigger token changes, test pattern and setup actions (incl. `POST /api/preflight/test_all`) are locked; `GET /api/preflight` stays open. Exception: turning calibration or test pattern **off** (`{on: false}`) passes. Unlock needs the PIN when set. |
| Lock vs live levels | `PUT /api/settings` passes under lock only for `master_brightness`/`master_volume`, body ≤ 4 KB. |
| Caps | Lowering a cap also clamps the current master value, so value ≤ cap always holds. |
| Fallback preset slot | WLED preset 250, set as boot preset. Manual save only (flash writes). Source: active profile base, else manual state, else error. Refused during show, blackout, setup. |
| Preflight interval | 10 s. WS push only when status or detail changes. |
| WiFi sleep check | Uses the last recommendations result, cached 6 h. Older or missing ⇒ ok "not checked". |
| mpv restart grace | Preflight shows warn "restarting" for 15 s after an mpv restart, not fail. |
| Setup actions | One at a time, cancellable. Blackout always wins. Show actions get 409 while one runs. |
| Stall recovery | Frozen `time-pos` ≥ 2 s ⇒ restart and resume once per play, then idle. |
| Calibration clip | `calibration-v1.mp4` in data dir: 1920x1080, 50 fps, 20 s, H.264+AAC. Black with a 0.1 s white flash and 1 kHz beep every even second. |
| Sync offsets | Per-board `offset_ms` (±500) adds to capped measured half-RTT. Cap applies to the measured part only. |
| Event log | `EventLog` table, 7-day retention, 1 s batched writes. Kinds: trigger, board, player. Refused triggers are not counted in stats. |
| Manual override clear | `DELETE /api/boards/{mac}/state` returns a board to the profile look (Show mode "Base"). |
| Audio device errors | mpv rejecting a device reverts the override and logs a warning. |
| Lock on ends setup | Turning the lock on cancels a running setup action (calibration, test sound, test-all) and turns the test pattern off, restoring idle. A forgotten one would refuse triggers all night. |
| mpv crash mid-show | Unrequested mpv restart while a show plays ⇒ resume at the last position, sharing the one-recovery-per-play budget with the stall watch. Second failure ⇒ idle. Outside a show, the current picture (idle clip, test pattern, calibration clip, black) is re-applied. |
| Player settings during show | `PUT /api/settings` changing `mpv_vo`, `mpv_gpu_context`, `mpv_drm_connector`, `mpv_drm_mode` or `mpv_audio_device` ⇒ 409 while a show plays or a setup action runs. |
| Blackout vs test pattern | Blackout turns the test pattern off; turning it on during blackout ⇒ 409. |
| Blackout vs fallback save | Blackout always reaches the board; a fallback save in progress aborts at its next step ("blackout active"). |
| Trigger seen | The preflight "recent trigger" check counts only accepted triggers from `POST /api/trigger` (source `external`). |
| Sync offset range | Global `sync_offset_ms` is limited to ±2000, finite. Stored out-of-range values are dropped on load. |
| Timeline markers | JSON column `Profile.markers`, not a table. Few per profile, always loaded with the profile, replaced as a whole. |
| Onset detection | Pure Python, no numpy: RMS envelope, log-energy flux, moving median + MAD peak picking. Keeps the hub free of heavy deps. |
| Cue clipboard | `localStorage` key `spookhub.clipboard`, `{v: 1, cues: [{offset_s, lanes, …}]}`. Works across profiles and tabs. Paste onto lanes that don't exist opens a remap dialog. |
| Frontend tests | vitest (`npm test`). Same toolchain as Vite. |
| Segment payload | Board and segment states merge into one `/json/state` request per board, so the latest-wins queue stays per board. `on`, `bri`, transition stay board-wide (WLED limit). |
| Cue templates | Parametric generators run client-side (TS). Saved templates are stored server-side (`CueTemplate`). |
| Timeline FPS | Fixed at 25. ffprobe data carries no frame rate. |
| Panel undo | Edits to the same cue within 800 ms coalesce into one undo step. |
| Audition mute | Muted lanes' boards get the profile base during audition. |
| Projector warp | User GLSL shader (`warp.glsl`) with `//!PARAM`s set via `glsl-shader-opts`, not `vf`/lavfi: no re-encode or filter rebuild, parameters change live. |
| Warp vs vo | `//!PARAM` needs vo=gpu-next. Default vo stays `gpu`; `DisplayState.warp_supported` and a preflight `warp` warning flag geometry that would not show. |
| Display precedence | Profile > media > global default > identity. A missing or invalid display profile falls through to the next. |
| Video setup mode | Reuses the test-pattern state, so the existing rules refuse triggers and setup actions and lock/blackout end it. |
| Leaving video setup | Unsaved live edits are dropped. Saving goes through display profile PUT/POST. |
| Crop storage | Fractions of the displayed (rotated) frame, mapped through rotation to source pixels for mpv `video-crop`. Stays valid across resolutions. |
| Alignment patterns | Rendered by ffmpeg lavfi on first use, cached per id and size in `<data_dir>/patterns/`. |
| Black check | Median of sampled YLOW (10th-percentile luma) ≤ 0.07 of full scale. Limited-range black is ≈ 0.063; a bright figure on black still passes. |
| Display profile export/import | Profile export carries the display profile as `{name, geometry}`. Import reuses an existing one with that name and never overwrites it; creates it only when missing. |
| Setup resolution | Media source on `/video`: media > global (you are setting up that video). Pattern and idle sources: active profile > media > global. Unsaved live edits survive a source switch. |
| Show vs setup | Play, play-from and trigger refuse (409) while video setup or a test pattern is on; leaving `/video` also clears a separately started test pattern. |
| Media rotation backfill | Rows from before `Media.rotation` are re-probed in the background at startup instead of assuming 0. |
| ffmpeg timeouts | Pattern render 60 s (503), black check 120 s (504); the process is killed on timeout or cancel. |
| Scene model | A scene is a profile type (`Profile.type = scene`, `Profile.scene` JSON), not its own table. Activation, cooldown, export/import and the lock reuse the profile paths. Scenes cannot contain scenes. |
| Audio mixing | Three mpv processes (main, `fx`, `ambient`) share the card through an ALSA `dmix` default written by `install.sh`. The fallback (mixing effects into main mpv via `af`/lavfi) is not built; revisit only if dmix fails on the hub. |
| Sounds per player | One sound at a time per effects player; a new play replaces the old. Overlapping sound cues: the latest-started covering cue wins, an older longer one resumes at its offset when it ends. Soundboard and cue sounds share `fx`. |
| Moment restore | Snapshot on press (active profile, timeline, phase, idle clip and ambient position). Restore without re-activation: idle clip resumes at the saved position, idle track and ambient carry on, touched boards return to the home base. Cooldown starts when the moment ends. The editor's test variant skips the cooldown. An interrupting moment keeps the first snapshot, so it restores what ran before both. |
| Scene chain after stop | A manual stop, blackout release or a failed item pauses a chain scene until the next trigger. No retry loop on a broken item. |
| Idle → main fade | Stepped mpv IPC: `brightness` (to -100) and `volume` every 20 ms over `fade_ms`. No filter rebuild. Ramp smoothness on the hub is an open spike. |
| Scene skip | Skip re-picks the next item; it does not stop a playing item. Pin wins once, then the picked next stays queued. |
| Moment light-only length | A light-only moment lasts its profile's `idle_length_s`, else 5 s. Video and sound parts: the longest wins. |
| Moment interrupting a show | A moment with `busy=interrupt` ends a running show without setting the show profile's cooldown. |
| Replacement ambience volume | The profile ambient's `volume` when the profile has an ambient, else 1.0. Not ducked. |
| Profile type change to light | Main-track cues are deleted (info event with the count); idle cues stay. |
| Jukebox queue | Own queue on the hub, one track per `PUT play {uris}`. Spotify's queue API can't remove or reorder. |
| Jukebox speaker | librespot (`raspotify`) on the hub as Spotify Connect device, ALSA `default` (dmix with mpv). Driven via the Web API with the host's Premium account. |
| Jukebox login | Authorization Code + PKCE, no client secret. Default redirect `http://127.0.0.1:<port>/…/callback` (Spotify allows http only on loopback); other browsers paste the address bar URL. |
| Jukebox admin | First guest to join is admin, so the host opens `/music` first. Hand-over via make_admin. `POST /api/jukebox_reset` frees it; it is protected only by the UI PIN, so set a PIN to stop guests resetting the host. |
| Jukebox fairness | Round-robin by guest (round = earlier pending songs of that guest). Bumps play first, latest bump first. |
| Jukebox vs show | Show playing or moment running ⇒ Spotify paused, resumed after. Skips refused meanwhile. |
| Jukebox other speakers | While another device of the account plays, the queue waits (`spotify.error` says where). A paused or idle account is taken over by the hub. Fallback or foreign tracks paused mid-song are not cut either. |
| Jukebox guest list | Sent on `/ws/jukebox` to the admin token only. |
| Jukebox skips | Vote and host skips name their target track; a skip whose track already changed is a no-op. Host skip without a target is debounced 1.5 s. |
| Backups vs jukebox | Backups drop Spotify tokens, guest token hashes and the admin. A restore keeps settings and queue; host and guests join or log in again. |
| Board groups resolve | Groups resolve to MACs when a profile loads, never on save. Group edits reload the home profile. |
| Board group overlap | Group cues follow the existing latest-start rule, not "segment > board > group > all" from the phase file: a priority rule would let an old open-ended board cue beat a later "all" blackout cue. |
| Group override order | Later group (sort order) merges on top of earlier ones; board keys merge on top of all group keys. |
| Moments, scenes and groups | No own board picker: moments and scenes reuse light/video profiles, which carry the room selection. |
| Test presets | Built in (`presets.py`), applied from Settings. Everything named `[Test] …`, upserted by name, removed by prefix only. |
| Group edits during a show | Allowed and applied live, like profile edits. Show lock is the guard. |
| Jukebox audio output | raspotify subprocess sink (aplay) reading the hub's audio device from a file, instead of rewriting ALSA config at runtime: no root at runtime, a Setup change applies on the next sink start (pause/resume). Shared per-port dmix outputs come from `install.sh`. |
| Jukebox dead speaker | A track gone before half its length played (no other track on the speaker) is requeued first and retried with backoff, not counted as ended: a crashing librespot used to burn the whole queue. |
| Jukebox queue end | Pause after the last song when no fallback is set: Spotify autoplay would otherwise play random songs. |
