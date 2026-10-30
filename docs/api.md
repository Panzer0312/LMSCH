# API and WebSocket

## REST

REST under `/api` (OpenAPI at `/docs`). Routers in `api/routes_*.py`:

| Router | Routes |
|---|---|
| auth | `GET status`, `POST login/logout`, `PUT pin`, `GET/PUT trigger_token` |
| boards | list, `scan`, `add`, `{mac}/adopt`, PATCH (`name`, `offset_ms`), DELETE, `identify`, `POST state`, `DELETE state` (clear manual override), `refresh`, `capture`, `recommendations[/{id}/apply]`, `POST fallback`, `POST {mac}/fallback` |
| profiles | CRUD, `duplicate`, `activate`, `PUT {pid}/cues` (scene ⇒ 422; light profile accepts only `track: idle` cues), `PUT {pid}/markers` (replace all, ids unique), `export` (adds `display_profile {name, geometry}`, scene items by profile name, ambient by `media_filename`), `import` (display profile matched by name, created if missing; scene items and ambient matched by name, missing ones dropped) |
| templates | `GET templates`, `POST templates {name, cues}`, `DELETE templates/{tid}` |
| media | list (adds `kind`, `rotation`, `aspect` = displayed w/h), upload (video or audio), `transcode`, `thumbs`, `thumbs.jpg`, `waveform`, `{mid}/onsets` (empty list if not derived), `{mid}/black_check` (cached, 422 on ffmpeg failure, 504 after 120 s), `file` (range), `PATCH {mid} {display_profile_id}`, DELETE. Audio: `transcode`/`black_check` ⇒ 422, `thumbs*` ⇒ 404 |
| display | `GET/POST profiles`, `PUT/DELETE profiles/{pid}` (409 on duplicate name), `GET state`, `POST setup {on, source?: pattern/media/idle}`, `POST live {geometry}`, `POST seek {t}` (media/idle source only), `GET patterns`, `GET patterns/{pid}/file` (PNG or mp4, 503 on render failure) |
| show | `GET state`, `POST trigger` (external, token), `show/trigger|play|stop|seek|pause|next|deactivate`, `blackout`, `GET/PUT settings`, `audition[/stop]` (body adds `muted: [mac]`), `profiles/{pid}/play_from` |
| scene | `GET scene` ⇒ `{profile_id, next_id, pinned, history, upcoming}` (10 ids), `POST scene/skip` (re-pick next), `POST scene/pin {profile_id\|null}`. 409 when no scene is active |
| sounds | `POST sounds/play {media_id, volume?}` (0–1.3, fx player, 409 in blackout, 503 when the player is not ready), `POST sounds/stop` |
| moments | `GET moments`, `GET moments/state`, `POST moments`, `PUT moments/{id}`, `DELETE moments/{id}`, `PUT moments/order [ids]` (every id once), `POST moments/{id}/play {variant?}` (variant = test, no cooldown), `POST moments/stop`. Play: 404 unknown, 422 bad variant, 409 refused (blackout, setup, test pattern, audition, cooldown, busy) |
| setup | `GET setup/outputs`, `GET setup/audio_devices`, `POST setup/test_sound`, `POST setup/calibration {on}`, `GET setup/triggers`, `GET preflight`, `POST preflight/test_all` |
| events | `GET events?since&kind&limit`, `GET events/stats?since` (triggers total + per hour) |
| admin | `show/test_pattern`, `POST show_lock {on, pin?}`, `GET backup`, `POST backup/restore` |

`GET /api/state` includes `version` (commit, install time). Setup actions return 409 while a show
plays or another setup action runs, 503 when mpv is unavailable. Fallback returns
`{results: [{mac, name, ok, error}]}`.

Moment validation (422): a variant needs a video, sound or light part; `video_profile_id` must be a
video profile, `light_profile_id` a light profile, sound and `ambience_media_id` audio media;
`ambience: replace` needs `ambience_media_id`. Scene validation (422): items unique, no scene in a
scene, not itself.

## WebSocket `/ws`

Message `{type, data, ts}`. Types: `snapshot` (on connect), `board`, `board_removed`,
`boards_changed`, `show`, `settings`, `event`, `profiles_changed`, `media_changed`, `transcode`,
`preflight` (`{checks, worst}`), `trigger_seen` (`last_trigger {ts, source, accepted, message}`),
`display` (`{setup, source, live, resolved_profile_id, geometry, warp_supported}`, on change only),
`display_profiles_changed`, `scene` (same payload as `GET /api/scene`, on change),
`moment` (`{playing_id, variant, progress 0–1, elapsed_s, duration_s, cooldown: {id: seconds left}}`,
values at send time; sent on change only: start, end, variant change, cooldown end; the client
advances progress from `elapsed_s` / `duration_s`, `null` = unknown length; `GET moments/state`
returns the same), `moments_changed` (list edited).
The snapshot also carries `version`, `preflight`, `display`, `scene` and `moment`.
Show snapshot adds `calibrating`, `setup_busy`, `last_trigger`.
Frontend reducer: `frontend/src/live.ts`. `display` has its own small store there (`useDisplay`), so ~30 Hz updates during drags only re-render video setup consumers.
