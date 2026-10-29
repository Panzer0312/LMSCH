# Roadmap

Five phases after M1–M3. Each phase ships deployable, tested, documented (see `AGENTS.md`).

| Phase | File | Theme | Status |
|---|---|---|---|
| 1 | [phase-1-show-night.md](phase-1-show-night.md) | Show-night reliability, device setup, sound/light helpers | built, hardware untested |
| 2 | [phase-2-timeline-qol.md](phase-2-timeline-qol.md) | Timeline editing quality of life | not started |
| 3 | [phase-3-video-setup.md](phase-3-video-setup.md) | Video setup page: geometry, keystone, display profiles, alignment patterns | not started |
| 4 | [phase-4-scenes-audio-ambient.md](phase-4-scenes-audio-ambient.md) | Scenes/playlists, audio-only cues, ambient layer, moments page | not started |
| 5 | [phase-5-triggers-integrations.md](phase-5-triggers-integrations.md) | Named triggers, schedule, MQTT/HA, relays, DMX | **optional, out of scope** |

## Order and dependencies

```
1 ──► 2 ──► 4 ··► 5 (optional)
 └──► 3 ─────┘
```

- Phase 1 first: hardware bring-up and preflight underpin everything.
- Phases 2 and 3 are independent (timeline vs player geometry) and can run in parallel.
- Phase 4 needs phase 2's lane abstraction (task 2.1) and phase 3's per-load hook (task 3.2).
- Phase 4.5 (moments page) builds on 4.1–4.3.
- Phase 5 is optional and out of scope for now. If started, it reuses phase 4's lane abstraction.

## How to execute a phase

1. Read the phase file and the docs it names.
2. Write the step-by-step implementation plan (superpowers:writing-plans) to
   `docs/roadmap/plans/phase-N-<group>.md`. Plans may be per task group.
3. Spikes marked **SPIKE** come first. Their result goes into the phase file before building on it.
4. Build, test, update docs, tick checkboxes, commit per task group.

## Conventions in phase files

- `- [ ]` task, ticked when merged with docs updated.
- **Accept:** what must be true to tick it.
- **SPIKE:** unknown to verify on real hardware/software before committing to a design.
- Files listed are expected touch points, not limits.
