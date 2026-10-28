# Phase 5: Named triggers, schedule, integrations

**Assumed scope.** The request listed phase 4 and was cut off before phase 5. This phase collects
the remaining brainstorm items. Re-scope freely.

## 5.1 Named triggers

- [ ] `Trigger` table: name, token, action (trigger active, activate profile/scene, play item, blackout), cooldown.
- [ ] `POST /api/trigger/{name}`; old `/api/trigger` maps to a default trigger (backwards compatible).
- [ ] Setup page lists triggers with last-seen and test buttons; preflight warns on silent triggers.
- **Accept:** PIR A starts scare 1, doorbell button starts scare 2.

## 5.2 Schedule

- [ ] Rules: time window or sunset/sunrise offset (own NOAA calc from lat/lon, no new dep) ⇒ action
  (activate, deactivate, blackout, brightness cap, volume cap).
- [ ] Preflight checks clock sync; schedule shown on dashboard ("blackout at 22:00").
- **Accept:** rule "dusk −15 min activate scene, 22:00 blackout" fires correctly in a clock-mocked test.

## 5.3 MQTT / Home Assistant

- [ ] Optional MQTT client (`aiomqtt`), off by default. Publishes show state and board health;
  subscribes trigger, blackout, profile select. Home Assistant discovery messages.

## 5.4 Device lanes: relays and HTTP actions

- [ ] Generic device = HTTP action (Shelly, Tasmota, any URL) with on/off payloads. Device lane on
  the timeline (phase 2/4 lane abstraction). Use: fog machine, animatronic, strobe on a socket.
- [ ] Safety: max on-time per device (fog machines), forced off on blackout and stop.

## 5.5 DMX

- [ ] Art-Net/sACN output over UDP (no driver), optional Enttec USB later. DMX fixtures as device lanes.

## 5.6 Stretch

- [ ] Multi-hub: second projector hub follows the first's clock over the LAN.

## Docs to update

All docs per `AGENTS.md`; new `docs/integrations.md` if 5.3–5.5 land.
