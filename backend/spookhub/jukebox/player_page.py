PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><title>Spookhub player</title></head>
<body>
<script>
const key = new URLSearchParams(location.search).get("key") || "";
const headers = { "X-Player-Key": key };
let first = null;

function retry(ms) {
  setTimeout(() => location.reload(), ms);
}

function report(error) {
  console.log("spookhub-player: " + (error || "ready"));
  fetch("/api/jukebox/player/status", {
    method: "POST",
    headers: { ...headers, "Content-Type": "application/json" },
    body: JSON.stringify({ error }),
  }).catch(() => {});
}

async function token() {
  const r = await fetch("/api/jukebox/player/token", { headers, cache: "no-store" });
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(body.detail || "HTTP " + r.status);
  return body;
}

window.onSpotifyWebPlaybackSDKReady = async () => {
  try {
    first = await token();
  } catch (e) {
    console.log("spookhub-player: no token: " + e.message);
    return retry(15000);
  }
  const player = new Spotify.Player({
    name: first.name,
    volume: 1.0,
    getOAuthToken: (cb) => {
      if (first) {
        const t = first.access_token;
        first = null;
        return cb(t);
      }
      token()
        .then((t) => cb(t.access_token))
        .catch((e) => {
          console.log("spookhub-player: token refresh failed: " + e.message);
          retry(15000);
        });
    },
  });
  player.addListener("ready", ({ device_id }) => {
    console.log("spookhub-player: device " + device_id);
    report(null);
  });
  player.addListener("not_ready", () => console.log("spookhub-player: offline"));
  player.addListener("initialization_error", ({ message }) => {
    report("Spotify player failed to start: " + message);
    retry(60000);
  });
  player.addListener("authentication_error", ({ message }) => {
    report("Spotify player login failed: " + message + ". Log out and connect Spotify again");
    retry(60000);
  });
  player.addListener("account_error", ({ message }) => {
    report("Spotify player needs Premium: " + message);
    retry(120000);
  });
  player.addListener("playback_error", ({ message }) => {
    console.log("spookhub-player: playback error: " + message);
  });
  const ok = await player.connect();
  if (!ok) retry(15000);
};
</script>
<script src="https://sdk.scdn.co/spotify-player.js" onerror="retry(15000)"></script>
</body></html>
"""
