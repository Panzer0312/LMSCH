# Part 1: First setup

Goal: hub installed, boards adopted and sorted into rooms, projector and sound working, every
board checked with the test presets.

## 1.1 Install or update the hub

On the hub (Debian 13):

```bash
cd ~/spookhub && git pull && sudo scripts/install.sh
```

`install.sh` is safe to rerun. It installs packages, builds the frontend, runs database
migrations and restarts the service. Details and options: [`operations.md`](../operations.md).

Open `http://<hub-ip>:8080` on a phone or laptop in the same WiFi. The Dashboard appears.
A red "Offline – reconnecting" bar means the browser lost the hub; it clears by itself.

Optional now, required before guests arrive: **Settings → Security → PIN**. Without a PIN anyone on
the WiFi can change the show.

## 1.2 Adopt boards

1. Power the WLED boards. They must be in the same network as the hub.
2. **Boards → Scan**. Found boards show under "Discovered". Not found? **Add by IP**.
3. Press **Adopt** on each and give a name you can read in the dark ("Porch left", "Window").
4. **Identify** flashes a board white for 2 s, so you know which one it is.
5. **Check settings** on each board. Fix "WiFi sleep" with one tap: it causes late cues.

The status dot is green (online), orange (slow) or red (offline). Latency should stay under
100 ms.

## 1.3 Make rooms

Rooms let one profile, cue or button drive a part of the house.

1. **Boards → Rooms**: type "Dining room", pick a colour, **Create**. Repeat for "Hall", "Porch".
2. On each board card tap the room chips it belongs to. A board may be in two rooms
   ("Dining room" and "Window side").
3. Order matters only when a board is in two rooms with different colours: the lower room in the
   list wins. Use ↑/↓.
4. Test a room: the room row has **On**, **Off**, brightness, **Identify** and **Auto**. Auto hands
   the boards back to the running profile.
5. Use the filter chips above the board list ("All", a room, "No room") to find stragglers.

Rooms are references. Moving a board into "Hall" later changes every profile, cue and Show-page
button that uses "Hall", without editing them.

## 1.4 Projector

**Setup → Projector output**:

1. Pick the connected output (listed first) and the mode (1920x1080 for most projectors).
   Applying blacks the screen for about 1 s.
2. **Test pattern** shows a grid. Off again before going on.
3. For a projector at an angle or on a scrim: **Open video setup** (`/video`).
   - **Start setup mode**, pick the "Numbered corners" pattern.
   - Drag corner handles 1–4 in the preview until the picture fits the surface.
   - Use the "Grey ramp" pattern and **Image → Brightness/Gamma** until black is invisible.
   - **Save as…** "Window scrim", then **Set as default**.
   - **Leave setup mode**.

Corners, mask and flip need `vo = gpu-next`; a banner warns when they have no effect.

## 1.5 Audio

**Setup → Audio output**: pick the device (HDMI or USB), set volume, **Play test sound**.
No sound? Try the next device in the list, then **reload devices**.

## 1.6 Trigger device (optional)

**Setup → Trigger devices** shows the token and ready-made curl/ESPHome snippets. **Send test
trigger** lights the "waiting for trigger" indicator. PIR sketches: [`docs/trigger/`](../trigger/).

## 1.7 Check everything with test presets

**Settings → Test presets**. Each button creates or updates `[Test]` items from the adopted boards.

| Tap | Then check |
|---|---|
| Basic looks | Activate each `[Test]` look on **Profiles**: all boards show the same colour or effect |
| Lightning storm | Activate it: dark blue, two double flashes every 10 s, all boards at once |
| Board chase | Boards flash one after the other in name order. Wrong order = wrong name or board |
| Two rooms | Rooms yellow/red: Room A yellow, Room B red. Edit the rooms to match your real ones |
| Scene + moment | Activate `[Test] Shuffle scene`, press **Trigger** a few times: a new look for 8 s, then candle. On `/moments` press ⚡ |
| Rehearsal caps | Brightness and volume limited for testing indoors |
| Silent check | Lights only, volume 0 |
| Show night | All caps back to 100 % |

Also run **Setup → Test everything**: all boards flash white, the test sound plays, the pattern
shows for 2 s, then the idle state returns.

When the hardware checks out: **Remove test items**. Applying a preset twice never duplicates.

Next: [Part 2: Build a show](02-build-a-show.md).
