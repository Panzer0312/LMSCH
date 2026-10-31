# Part 2: Build a show

Goal: an idle look between visitors, a scare video with light and sound cues, a playlist of
scares, and moment buttons.

## 2.1 Upload media

**Media → Upload**. Videos (mp4, mkv, mov, …) and sounds (mp3, wav, ogg, …).

- A "needs transcode" badge (not H.264/HEVC, or above 1080p) ⇒ press **Transcode**. The hub plays
  H.264 1080p smoothly.
- **Black check** on scare videos for a scrim: a grey background shows as a rectangle in the dark.
- The aspect badge warns when a video differs from the projector. "Create display profile for this
  video" fixes it for that file only.

## 2.2 A light profile (idle look)

**Profiles → New**, type **Light**.

1. Name: "Idle candle".
2. Boards: **All boards**, or tap rooms ("Dining room") and single boards.
3. Base state: effect "Candle", orange, brightness ~40 %.
4. **Per-room overrides**: "Hall" red, "Porch" off. Per-board overrides beat room overrides.
5. Optional **Ambient sound loop**: wind or crows, "Duck to during scare" 30 %.
6. Optional **Idle loop length (s)**, e.g. 12: then the timeline Idle tab adds breathing or
   flicker cues that loop.
7. Save, then **Activate**.

## 2.3 A video profile (the scare)

**Profiles → New**, type **Video**.

1. Pick the scare video and an idle video (or none = black between scares).
2. Playback mode **idle_trigger**: idle until a trigger, play once, back to idle. Cooldown 20 s
   stops a lingering visitor re-triggering.
3. **Fade idle → scare** 400 ms is a soft cut; 0 is a hard cut.
4. Save, then **Open timeline editor**.

## 2.4 Timeline: light cues

Lanes: "All selected", then one lane per room, one per board, then Sound.

1. Press Space to play the preview. Press **M** at each scary beat to drop a marker.
2. Double-click a lane at a time to add a cue there. A cue on a room lane follows the room.
3. In the cue panel: effect, colour, brightness, duration (empty = hold until the next cue).
4. **Templates…** → "lightning" or "strobe burst" adds a ready pattern at the playhead, with a
   live preview on the ribbon.
5. Onsets: the ticks on the waveform are loud hits. **Generate cues…** makes one cue per hit.
6. **Live audition**: the real boards follow the preview. M/S on a lane mutes or solos it, also on
   room lanes.
7. Overlap badges show cues fighting on the same board. Rule: the cue that started last wins.

Changes save 0.8 s after the last edit. Keys: [`timeline.md`](../timeline.md).

## 2.5 Sound cues

On the Sound lane: **+ Sound** at the playhead, pick a scream or thunder. Set volume and fades.
One sound plays at a time; the latest one wins. The ambient loop ducks during the scare video.

## 2.6 Sync

**Setup → Sync calibration** loops a clip; boards flash white on each beep. Move the global offset
until flash and beep match, then per-board offsets for boards that lag. Do this once on the real
WiFi with all boards on.

## 2.7 A scene (several scares)

**Profiles → New**, type **Scene**.

1. Add the video profiles as items. Order **Shuffle** never plays the same one twice in a row.
2. Idle profile: "Idle candle" (or a video idle profile).
3. Light items hold for "light hold (s)".
4. Activate. Each trigger plays the next pick, then the idle part continues where it was.
5. Dashboard scene card: **Skip** re-picks, **Pin next** forces the next scare.

## 2.8 Moments (buttons)

`/moments` → **Edit** → **+ Add moment**.

1. Icon 🔔, name "Doorbell", colour.
2. Variant: video profile "Dracula", sound "creak", light profile "Blood pulse", ambience **Mute**.
   Add more variants for surprise; they shuffle.
3. When busy: **Queue** (plays after the current scare), **Ignore** or **Interrupt**.
4. Cooldown 30 s. **Test** plays the variant now.
5. **Done**. **Kiosk** puts the buttons full-screen on a tablet by the door.

A moment's light part uses a light profile, so it can drive one room only: pick a profile whose
boards are that room.

## 2.9 Save your work

**Settings → Backup → Download backup** (database + config, no media). Profile **Export** saves one
profile as JSON for another hub; rooms are matched by name there.

Next: [Part 3: Show night](03-show-night.md).
