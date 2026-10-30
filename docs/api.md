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
| jukebox | `POST jukebox_reset`: clears the jukebox admin. Guarded only by the UI PIN: without a PIN any guest can call it. Guest routes below |

`GET /api/state` includes `version` (commit, install time). Setup actions return 409 while a show
plays or another setup action runs, 503 when mpv is unavailable. Fallback returns
`{results: [{mac, name, ok, error}]}`.

Moment validation (422): a variant needs a video, sound or light part; `video_profile_id` must be a
video profile, `light_profile_id` a light profile, sound and `ambience_media_id` audio media;
`ambience: replace` needs `ambience_media_id`. Scene validation (422): items unique, no scene in a
scene, not itself.

## Jukebox `/api/jukebox/*`

No PIN, allowed in Show mode. Guest token in header `X-Jukebox-Token` (from `join`); missing or
unknown ⇒ 401 `join first`. Admin routes by a guest ⇒ 403. Errors: `{detail: "<user text>"}`.

| Route | Notes |
|---|---|
| `POST join {name}` | 1–24 chars after trimming. With a valid token: rename. First join without an admin ⇒ admin. `{token, guest {id, name}, role}` |
| `GET me`, `GET state` | `{guest, role}`; `JukeboxState` (open, no token) |
| `GET search?q=` | 1–200 chars, ≤ 10 tracks + `queued`. Track URL/URI ⇒ that track (`[]` if unknown). 503 not connected |
| `POST queue {uri}` | Track URI or URL; metadata re-fetched. 403 banned, 409 closed/full (200)/duplicate/limit, 422 not a track/explicit/too long, 404 unknown track, 503 not connected |
| `DELETE queue/{id}` | Own entry or admin, else 403; 404 gone |
| `POST vote_skip` | `{ok, skipped, votes, votes_needed}`. Requester and admin skip at once. Votes target the track playing when sent; `skipped: false` if it already changed. 409 nothing playing or paused for the show |
| `POST admin/skip\|pause\|resume\|clear`, `POST admin/queue/{id}/bump` | `{ok}`. Skip takes optional `{uri}`: skips only if that track still plays; without it a skip within 1.5 s of the last skip is ignored (double tap). Skip 409 during the show, 503 speaker not found |
| `PUT admin/settings` | Partial; `fallback_uri` playlist/album URI or URL (normalized, `null`/`""` clears, else 422). Returns settings |
| `GET admin/guests`, `POST admin/guests/{gid}/ban {banned}`, `POST …/make_admin` | Ban drops pending songs; the admin can't be banned (409) |
| `PUT admin/spotify {client_id?, device_name?}` | A new client id drops the login |
| `GET admin/spotify/login` ⇒ `{url}`, `POST admin/spotify/code {url}` | PKCE. `code` takes the pasted redirect address; unknown/used `state` ⇒ 400 |
| `GET spotify/callback?code&state` | 302 `/music?spotify=ok` or `?spotify=error` |
| `POST admin/spotify/logout`, `GET admin/spotify/devices` | Logout pauses the speaker first (best effort). Devices `[{id, name, type, is_active}]` |

`JukeboxState`: `spotify {client_id_set, connected, account, device_name, device_found,
redirect_uri, error}`, `now` (track + `progress_ms, at, is_playing, entry_id, requested_by, votes,
votes_needed`), `queue` (play order), `history`, `paused` (`admin`/`show`/null), `settings`,
`guests_active` (seen in 30 min), `admin_id`, `port80`. Never contains tokens or hashes.
`spotify.error`: the transient error (e.g. playing on another device), else a lasting notice (no
Premium). The host should open `/music` first so they become admin.

WS `/ws/jukebox?token=` (no PIN): `snapshot {jukebox}`, then `jukebox` (state) and, for the admin
token only, `jukebox_guests` (guest list). Incoming text is ignored. `token`, `code` and `state`
query values are redacted from logs.

## WebSocket `/ws`

Message `{type, data, ts}`. Types: `snapshot` (on connect), `board`, `board_removed`,
`boards_changed`, `show`, `settings`, `event`, `profiles_changed`, `media_changed`, `transcode`,
`preflight` (`{checks, worst}`), `trigger_seen` (`last_trigger {ts, source, accepted, message}`),
`display` (`{setup, source, live, resolved_profile_id, geometry, warp_supported}`, on change only),
`display_profiles_changed`, `scene` (same payload as `GET /api/scene`, on change),
`moment` (`{playing_id, variant, progress 0–1, elapsed_s, duration_s, cooldown: {id: seconds left}}`,
values at send time; sent on change only: start, end, variant change, cooldown end; the client
advances progress from `elapsed_s` / `duration_s`, `null` = unknown length; `GET moments/state`
returns the same), `moments_changed` (list edited), `jukebox` (state), `jukebox_guests`.
The snapshot also carries `version`, `preflight`, `display`, `scene`, `moment` and `jukebox`.
Show snapshot adds `calibrating`, `setup_busy`, `last_trigger`.
Frontend reducer: `frontend/src/live.ts`. `display` has its own small store there (`useDisplay`), so ~30 Hz updates during drags only re-render video setup consumers.
