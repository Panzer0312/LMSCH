# Spookhub tutorial

Hands-on walk-through from an empty hub to a running Halloween show. Reference docs describe each
feature in detail; this tutorial shows the order to do things in.

| Part | Covers | Time |
|---|---|---|
| [1. First setup](01-first-setup.md) | Install, open the UI, adopt boards, rooms, projector, audio, test presets | 30 min |
| [2. Build a show](02-build-a-show.md) | Media, light and video profiles, timeline, sound, scenes, moments | 1–2 h |
| [3. Show night](03-show-night.md) | Checklist, calibration, show mode, lock, during the night, troubleshooting | 20 min |

## Words used

| Word | Meaning |
|---|---|
| Hub | The mini PC running Spookhub, the projector and the speakers |
| Board | One WLED controller (ESP32) with an LED strip |
| Room | A named group of boards (Boards page → Rooms). A board can be in several rooms |
| Profile | What runs: a light look, a video with cues, or a scene (playlist) |
| Cue | A light change at a time on a profile's timeline |
| Trigger | Starts the scare: Trigger button, PIR sensor, doorbell (`POST /api/trigger`) |
| Moment | One-shot button on `/moments` (doorbell scare, thunder) that returns to what ran before |

## Fastest path (10 minutes, real boards)

1. Update the hub: `cd ~/spookhub && git pull && sudo scripts/install.sh`.
2. Open `http://<hub-ip>:8080/boards`, press **Scan**, **Adopt** each board with a name.
3. **Settings → Test presets**: tap **Basic looks**, **Board chase**, **Two rooms**,
   **Scene + moment**.
4. **Profiles**: activate `[Test] Board chase`. Each board flashes in turn: names and order are right.
5. Activate `[Test] Rooms yellow/red`: half the boards yellow, half red.
6. Open `/moments`, press **⚡ Lightning strike**: flashes, then the previous look returns.
7. **Settings → Test presets → Remove test items** when done. Your own profiles stay.

Next: [Part 1](01-first-setup.md).
