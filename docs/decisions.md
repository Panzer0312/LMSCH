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
