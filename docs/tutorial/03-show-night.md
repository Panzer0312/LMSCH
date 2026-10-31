# Part 3: Show night

## 3.1 Two hours before

| Step | Where | OK when |
|---|---|---|
| Update | hub: `git pull && sudo scripts/install.sh` | Settings footer shows the new commit |
| Boards on | Boards | All green, latency < 100 ms |
| Preflight | Dashboard banner / Setup | No red check. Read the orange ones |
| Test everything | Setup | Flash, sound and pattern all seen |
| Sync | Setup → Sync calibration | Flash and beep match |
| Caps | Settings → Test presets → **Show night** (or set caps by hand) | Full brightness and volume |
| Fallback | Boards → **Save fallback** (all) | A board that reboots comes back in the idle look without the hub |
| Backup | Settings → Backup | File downloaded |
| PIN | Settings → Security | Set, so guests on the WiFi can't edit |
| Test items | Settings → Test presets → Remove test items | No `[Test]` profiles left |

## 3.2 Start the show

1. Activate the scene or video profile (Dashboard chips or Profiles).
2. Open `/show` on the operator phone. Big buttons: Trigger, Play/Stop, Next, Blackout,
   brightness, volume.
3. Turn **Lock** on. Editing is refused; show controls, moments, room and board buttons still work.
4. Put `/moments` in **Kiosk** on a tablet if you use buttons.
5. Guests' music: `http://<hub-ip>/music` (see the Jukebox host tab). Music pauses during scares.

## 3.3 During the night

| Situation | Do |
|---|---|
| A room too bright or broken | `/show` → that room's **Off** or **White**. **Auto** returns it to the show |
| One board misbehaves | `/show` → that board's Off. Boards → Identify to find it |
| Kids scared too much | Moments with Interrupt end the current scare; **Blackout** stops everything |
| Scare repeats too often | Raise the profile cooldown (unlock first) |
| No trigger for a while | Preflight warns "recent trigger" after 30 min (sensor dead?) |
| Video freezes | The hub restarts and resumes once, then goes idle by itself |
| Hub WiFi drops | Boards keep the last look; after a reboot they load the saved fallback |

Blackout holds everything dark until released. A failed blackout shows "FAILED – RETRY".

## 3.4 After the night

- Dashboard → **History**: triggers tonight, per hour.
- Download a backup.
- Turn Lock off to edit again.

## 3.5 Troubleshooting

| Symptom | Check |
|---|---|
| Board offline | Power, WiFi range, Boards → refresh. WiFi sleep fixed? |
| Cues late on one board | Sync calibration per-board offset; move the board closer to the AP |
| No picture | Setup → Projector output connector; Settings mpv output |
| Keystone does nothing | Banner "warp": set mpv `vo` to `gpu-next` in Settings |
| No sound | Setup → Audio output device; volume caps in Settings |
| Room cue does nothing | Preflight "Board groups": room empty or deleted. Boards → Rooms |
| 423 "Show lock is on" | Unlock on `/show` (PIN if set) |
| Jukebox silent | Host tab: Spotify connected, speaker "Spookhub" picked once in the Spotify app |

More: [`operations.md`](../operations.md) (paths, logs, hardware).
