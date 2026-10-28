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
