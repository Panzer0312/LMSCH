from __future__ import annotations

import asyncio
import json
import time
from urllib.parse import parse_qs, urlparse

import pytest

from spookhub.jukebox.fairqueue import ordered
from spookhub.jukebox.service import JukeboxError, normalize_context, parse_track_id
from spookhub.jukebox.spotify import SpotifyError
from tests.conftest import wait_for
from tests.jukebox_fake import connect, make_service, tid, track

ID = "4uLU6hMCjMI75M1A2tKUQC"


def e(seq: int, guest: str, bumped: int | None = None) -> dict[str, object]:
    return {"id": seq, "uri": f"u{seq}", "guest_id": guest, "added_seq": seq, "bumped_seq": bumped}


# ----- pure -----


def test_fairqueue_round_robin() -> None:
    entries = [e(1, "a"), e(2, "a"), e(3, "a"), e(4, "b"), e(5, "c"), e(6, "b")]
    assert [x["id"] for x in ordered(entries)] == [1, 4, 5, 2, 6, 3]


def test_fairqueue_bumped_first_latest_bump_wins() -> None:
    entries = [e(1, "a"), e(2, "b"), e(3, "a", bumped=7), e(4, "c", bumped=9)]
    assert [x["id"] for x in ordered(entries)] == [4, 3, 1, 2]
    assert ordered([]) == []


def test_parse_track_and_context() -> None:
    assert parse_track_id(f"spotify:track:{ID}") == ID
    assert parse_track_id(f"https://open.spotify.com/track/{ID}?si=abc") == ID
    assert parse_track_id(f"https://open.spotify.com/intl-de/track/{ID}") == ID
    assert parse_track_id(f"open.spotify.com/track/{ID}") == ID
    assert parse_track_id(f"https://open.spotify.com/playlist/{ID}") is None
    assert parse_track_id("hello world") is None
    assert normalize_context(f"https://open.spotify.com/playlist/{ID}?si=x") == (
        f"spotify:playlist:{ID}"
    )
    assert normalize_context(f"spotify:album:{ID}") == f"spotify:album:{ID}"
    assert normalize_context("  ") is None
    with pytest.raises(ValueError):
        normalize_context(f"spotify:track:{ID}")


# ----- guests -----


async def test_join_first_admin_make_admin_reset(cfg) -> None:
    svc, _, _ = make_service(cfg)
    a = svc.join("  Host  ", None)
    assert a["role"] == "admin" and a["guest"]["name"] == "Host"
    b = svc.join("Bob", None)
    assert b["role"] == "guest"
    # Same token: rename, same guest.
    b2 = svc.join("Bobby", b["token"])
    assert b2["guest"]["id"] == b["guest"]["id"] and b2["token"] == b["token"]
    with pytest.raises(JukeboxError) as exc:
        svc.join("x" * 25, None)
    assert exc.value.status == 422
    svc.make_admin(b["guest"]["id"])
    assert svc.admin_id == b["guest"]["id"]
    svc.reset_admin()
    assert svc.admin_id is None
    # Next join with a valid token takes admin.
    assert svc.join("Host", a["token"])["role"] == "admin"
    # Persisted, token only as hash.
    svc2, _, _ = make_service(cfg)
    assert svc2.admin_id == a["guest"]["id"]
    assert svc2.guest_for(a["token"])["name"] == "Host"
    assert svc2.guest_for("nope") is None


async def test_no_secrets_in_state(cfg) -> None:
    svc, _, _ = make_service(cfg)
    connect(svc)
    a = svc.join("Host", None)
    blob = json.dumps(svc.state()) + json.dumps(svc.guests_list())
    for secret in (a["token"], svc.guests[a["guest"]["id"]]["token_hash"], "AT0", "RT0"):
        assert secret not in blob


# ----- queue rules -----


async def test_add_rules(cfg) -> None:
    svc, fake, _ = make_service(cfg)
    host = svc.guest_for(svc.join("Host", None)["token"])
    bob = svc.guest_for(svc.join("Bob", None)["token"])
    with pytest.raises(JukeboxError) as exc:
        await svc.add(bob, f"spotify:track:{tid(1)}")
    assert exc.value.status == 503  # Spotify not connected
    connect(svc)
    svc.settings["per_guest_limit"] = 2
    entry = await svc.add(bob, f"https://open.spotify.com/track/{tid(1)}?si=x")
    assert entry["uri"] == f"spotify:track:{tid(1)}" and entry["title"] == "Song 1"
    assert entry["guest"] == {"id": bob["id"], "name": "Bob"}

    async def status(g, uri) -> int:
        with pytest.raises(JukeboxError) as exc:
            await svc.add(g, uri)
        return exc.value.status

    assert await status(bob, f"spotify:track:{tid(1)}") == 409  # duplicate
    await svc.add(bob, f"spotify:track:{tid(2)}")
    assert await status(bob, f"spotify:track:{tid(3)}") == 409  # limit
    await svc.add(host, f"spotify:track:{tid(3)}")
    await svc.add(host, f"spotify:track:{tid(4)}")
    await svc.add(host, f"spotify:track:{tid(5)}")  # admin unlimited
    assert await status(bob, "spotify:album:xyz") == 422
    fake.tracks[tid(6)] = track(6, explicit=True)
    fake.tracks[tid(7)] = track(7, duration_ms=11 * 60_000)
    svc.settings["per_guest_limit"] = 20
    svc.settings["allow_explicit"] = False
    assert await status(bob, f"spotify:track:{tid(6)}") == 422
    assert await status(bob, f"spotify:track:{tid(7)}") == 422
    assert await status(bob, f"spotify:track:{'Z' * 22}") == 404
    svc.settings["accepting"] = False
    assert await status(bob, f"spotify:track:{tid(8)}") == 409
    await svc.add(host, f"spotify:track:{tid(8)}")  # host may add while closed
    svc.ban(bob["id"], True)
    assert await status(bob, f"spotify:track:{tid(9)}") == 403
    assert all(x["guest_id"] != bob["id"] for x in svc.queue)  # ban drops pending songs
    with pytest.raises(JukeboxError):
        svc.ban(host["id"], True)


async def test_remove_own_vs_other(cfg) -> None:
    svc, _, _ = make_service(cfg)
    connect(svc)
    host = svc.guest_for(svc.join("Host", None)["token"])
    bob = svc.guest_for(svc.join("Bob", None)["token"])
    eve = svc.guest_for(svc.join("Eve", None)["token"])
    e1 = await svc.add(bob, f"spotify:track:{tid(1)}")
    e2 = await svc.add(bob, f"spotify:track:{tid(2)}")
    with pytest.raises(JukeboxError) as exc:
        svc.remove(eve, e1["id"])
    assert exc.value.status == 403
    svc.remove(bob, e1["id"])
    svc.remove(host, e2["id"])
    assert svc.queue == []
    with pytest.raises(JukeboxError) as exc:
        svc.remove(host, 999)
    assert exc.value.status == 404


async def test_search_marks_queued(cfg) -> None:
    svc, _, _ = make_service(cfg)
    connect(svc)
    bob = svc.guest_for(svc.join("Bob", None)["token"])
    await svc.add(bob, f"spotify:track:{tid(2)}")
    res = await svc.search("song")
    assert len(res) == 9
    assert {r["uri"] for r in res if r["queued"]} == {f"spotify:track:{tid(2)}"}
    one = await svc.search(f"https://open.spotify.com/intl-de/track/{tid(3)}?si=1")
    assert [r["title"] for r in one] == ["Song 3"]


# ----- playback -----


async def test_advance_and_end_of_track(cfg) -> None:
    svc, fake, _ = make_service(cfg)
    connect(svc)
    bob = svc.guest_for(svc.join("Bob", None)["token"])
    await svc.add(bob, f"spotify:track:{tid(1)}")
    await svc.add(bob, f"spotify:track:{tid(2)}")
    await svc._tick()  # nothing playing, queue non-empty
    assert fake.play_calls() == [{"uris": [f"spotify:track:{tid(1)}"]}]
    assert "PUT /me/player" in fake.paths()  # transfer before first play
    assert svc.current["uri"] == f"spotify:track:{tid(1)}"
    assert svc.history[0]["title"] == "Song 1" and len(svc.queue) == 1
    st = svc.state()
    assert st["now"]["requested_by"]["name"] == "Bob" and st["now"]["is_playing"]

    fake.set_playing(1, 60_000)
    assert await svc._tick() == cfg.jukebox.poll_active_s
    fake.set_playing(1, 180_000 - 2000)
    delay = await svc._tick()
    assert 1.6 < delay < 1.8  # sleeps until ~300 ms before the end
    fake.set_playing(1, 180_000 - 200)
    await svc._tick()
    assert fake.play_calls()[-1] == {"uris": [f"spotify:track:{tid(2)}"]}

    # External pause mid-track: reflect, do not advance.
    await svc.add(bob, f"spotify:track:{tid(3)}")
    fake.set_playing(2, 50_000, playing=False)
    n = len(fake.play_calls())
    await svc._tick()
    assert len(fake.play_calls()) == n and svc.state()["now"]["is_playing"] is False
    # Played to near its end, then stopped at 0: finished, advance.
    fake.set_playing(2, 170_000)
    await svc._tick()
    fake.set_playing(2, 0, playing=False)
    await svc._tick()
    assert fake.play_calls()[-1] == {"uris": [f"spotify:track:{tid(3)}"]}


async def test_dead_speaker_keeps_queue(cfg) -> None:
    svc, fake, _ = make_service(cfg)
    connect(svc)
    bob = svc.guest_for(svc.join("Bob", None)["token"])
    await svc.add(bob, f"spotify:track:{tid(1)}")
    await svc.add(bob, f"spotify:track:{tid(2)}")
    await svc._tick()
    # librespot died seconds into the song: playback gone. Song 1 goes back first, no advance.
    fake.playback = None
    delay = await svc._tick()
    assert delay == svc.LOST_RETRY_S and svc.current is None
    assert [x["uri"] for x in ordered(svc.queue)] == [
        f"spotify:track:{tid(1)}",
        f"spotify:track:{tid(2)}",
    ]
    assert "stopped playing" in svc.state()["spotify"]["error"]
    assert len(fake.play_calls()) == 1 and len(svc.history) == 0
    # Retry plays song 1 again; error stays until it really plays.
    await svc._tick()
    assert fake.play_calls()[-1] == {"uris": [f"spotify:track:{tid(1)}"]}
    assert svc.state()["spotify"]["error"]
    fake.set_playing(1, 20_000)
    await svc._tick()
    assert svc.state()["spotify"]["error"] is None


async def test_queue_end_pauses_against_autoplay(cfg) -> None:
    svc, fake, _ = make_service(cfg)
    connect(svc)
    bob = svc.guest_for(svc.join("Bob", None)["token"])
    await svc.add(bob, f"spotify:track:{tid(1)}")
    await svc._tick()
    fake.set_playing(1, 180_000 - 100)
    await svc._tick()
    assert fake.paths()[-1] == "PUT /me/player/pause"
    assert svc.state()["now"] is None
    await svc._tick()  # the paused last track stays hidden
    assert svc.state()["now"] is None
    # Late poll: autoplay already started a random song; it is paused too.
    await svc.add(bob, f"spotify:track:{tid(2)}")
    await svc._tick()
    fake.set_playing(2, 176_000)
    await svc._tick()
    fake.set_playing(7, 2_000)
    await svc._tick()
    assert fake.paths()[-1] == "PUT /me/player/pause" and svc.state()["now"] is None
    # A new song starts normally.
    await svc.add(bob, f"spotify:track:{tid(3)}")
    await svc._tick()
    assert fake.play_calls()[-1] == {"uris": [f"spotify:track:{tid(3)}"]}


async def test_start_grace_ignores_stale_playback(cfg) -> None:
    svc, fake, _ = make_service(cfg)
    connect(svc)
    svc.START_GRACE_S = 10.0
    bob = svc.guest_for(svc.join("Bob", None)["token"])
    await svc.add(bob, f"spotify:track:{tid(1)}")
    await svc.add(bob, f"spotify:track:{tid(2)}")
    await svc._tick()
    fake.set_playing(5, 179_900)  # Spotify still reports the old track
    await svc._tick()
    assert len(fake.play_calls()) == 1
    assert svc.state()["now"]["uri"] == f"spotify:track:{tid(1)}"


async def test_fallback_not_cut_mid_song(cfg) -> None:
    svc, fake, _ = make_service(cfg)
    connect(svc)
    bob = svc.guest_for(svc.join("Bob", None)["token"])
    svc.update_settings({"fallback_uri": f"https://open.spotify.com/playlist/{ID}"})
    assert svc.settings["fallback_uri"] == f"spotify:playlist:{ID}"
    await svc._tick()
    assert fake.play_calls() == [{"context_uri": f"spotify:playlist:{ID}"}]
    assert "PUT /me/player/shuffle" in fake.paths()
    await svc.add(bob, f"spotify:track:{tid(1)}")
    fake.playback["progress_ms"] = 30_000
    await svc._tick()
    assert len(fake.play_calls()) == 1  # fallback keeps playing
    fake.playback["progress_ms"] = fake.playback["item"]["duration_ms"] - 100
    await svc._tick()
    assert fake.play_calls()[-1] == {"uris": [f"spotify:track:{tid(1)}"]}

    # Fallback moved on to its next track: advance then too.
    svc.current = None
    fake.set_playing(98, 10_000)
    await svc._tick()
    await svc.add(bob, f"spotify:track:{tid(2)}")
    fake.set_playing(97, 1_000)
    await svc._tick()
    assert fake.play_calls()[-1] == {"uris": [f"spotify:track:{tid(2)}"]}


async def test_admin_skip_cuts_fallback_and_clears_pause(cfg) -> None:
    svc, fake, _ = make_service(cfg)
    connect(svc)
    host = svc.guest_for(svc.join("Host", None)["token"])
    svc.update_settings({"fallback_uri": f"spotify:album:{ID}"})
    await svc._tick()
    await svc.add(host, f"spotify:track:{tid(4)}")
    await svc.admin_pause()
    assert svc.paused == "admin"
    await svc.skip()
    assert fake.play_calls()[-1] == {"uris": [f"spotify:track:{tid(4)}"]} and svc.paused is None
    await svc.skip()  # queue empty: restart the fallback
    assert fake.play_calls()[-1] == {"context_uri": f"spotify:album:{ID}"}


async def test_vote_skip_threshold(cfg) -> None:
    svc, fake, _ = make_service(cfg)
    connect(svc)
    gs = [svc.guest_for(svc.join(f"G{i}", None)["token"]) for i in range(6)]
    host, req = gs[0], gs[1]
    await svc.add(req, f"spotify:track:{tid(1)}")
    await svc.add(req, f"spotify:track:{tid(2)}")
    await svc._tick()
    svc.settings["skip_vote_ratio"] = 0.5
    assert svc.votes_needed() == 3  # ceil(6 x 0.5)
    r = await svc.vote_skip(gs[2])
    assert r == {"ok": True, "skipped": False, "votes": 1, "votes_needed": 3}
    assert (await svc.vote_skip(gs[2]))["votes"] == 1  # one vote per guest
    await svc.vote_skip(gs[3])
    r = await svc.vote_skip(gs[4])
    assert r["skipped"] and fake.play_calls()[-1] == {"uris": [f"spotify:track:{tid(2)}"]}
    assert svc.votes == set()  # reset per track
    # Requester skips own song at once; admin too.
    await svc.add(req, f"spotify:track:{tid(3)}")
    assert (await svc.vote_skip(req))["skipped"]
    await svc.add(req, f"spotify:track:{tid(4)}")
    assert (await svc.vote_skip(host))["skipped"]
    # Inactive guests do not count; at least 2 votes.
    for g in gs[2:]:
        g["last_seen"] = time.time() - 4000
    assert svc.votes_needed() == 2


async def test_show_pauses_and_resumes(cfg) -> None:
    svc, fake, busy = make_service(cfg)
    connect(svc)
    bob = svc.guest_for(svc.join("Bob", None)["token"])
    await svc.add(bob, f"spotify:track:{tid(1)}")
    await svc.add(bob, f"spotify:track:{tid(2)}")
    await svc._tick()
    fake.set_playing(1, 60_000)
    busy["v"] = True
    await svc._tick()
    assert svc.paused == "show" and "PUT /me/player/pause" in fake.paths()
    assert fake.playback["is_playing"] is False
    assert len(fake.play_calls()) == 1  # no advance while paused
    with pytest.raises(JukeboxError):
        await svc.skip()
    busy["v"] = False
    svc.START_GRACE_S = 5.0  # Spotify reports the resume late
    await svc._tick()
    assert svc.paused is None
    assert fake.play_calls()[-1] is None  # resume (PUT play without body)


async def test_show_keeps_admin_pause(cfg) -> None:
    svc, fake, busy = make_service(cfg)
    connect(svc)
    bob = svc.guest_for(svc.join("Bob", None)["token"])
    await svc.add(bob, f"spotify:track:{tid(1)}")
    await svc._tick()
    await svc.admin_pause()
    busy["v"] = True
    await svc._tick()
    assert svc.paused == "show"
    busy["v"] = False
    await svc._tick()
    assert svc.paused == "admin" and fake.play_calls()[-1] is not None


async def test_missing_device_keeps_queue(cfg) -> None:
    svc, fake, _ = make_service(cfg)
    connect(svc)
    fake.devices = [{"id": "x", "name": "Phone", "type": "Smartphone"}]
    bob = svc.guest_for(svc.join("Bob", None)["token"])
    await svc.add(bob, f"spotify:track:{tid(1)}")
    await svc._tick()
    st = svc.state()
    assert st["spotify"]["device_found"] is False and len(st["queue"]) == 1
    fake.devices[0]["name"] = "SPOOKHUB"  # case-insensitive
    await svc._tick()
    assert svc.state()["spotify"]["device_found"] and fake.play_calls()


# ----- Spotify client -----


async def test_pkce_paste_url_and_state_check(cfg) -> None:
    svc, fake, _ = make_service(cfg)
    with pytest.raises(JukeboxError):
        svc.login_url()  # no client id
    svc.set_spotify("a" * 32, None)
    url = svc.login_url()
    q = parse_qs(urlparse(url).query)
    assert q["code_challenge_method"] == ["S256"] and q["client_id"] == ["a" * 32]
    assert q["redirect_uri"] == [svc.redirect_uri]
    state = q["state"][0]
    cb = f"{svc.redirect_uri}?code=abc&state=wrong"
    with pytest.raises(JukeboxError) as exc:
        await svc.finish_from_url(cb)
    assert exc.value.status == 400
    with pytest.raises(JukeboxError) as exc:
        await svc.finish_from_url("http://127.0.0.1/nothing")
    assert exc.value.status == 422
    await svc.finish_from_url(f"{svc.redirect_uri}?code=abc&state={state}")
    form = fake.token_forms[-1]
    assert form["code_verifier"] and form["code"] == ["abc"] and "client_secret" not in form
    st = svc.state()["spotify"]
    assert st["connected"] and st["account"] == "Host" and st["error"] is None
    with pytest.raises(JukeboxError):  # state is single-use
        await svc.finish_from_url(f"{svc.redirect_uri}?code=abc&state={state}")
    await svc.logout()
    assert not svc.state()["spotify"]["connected"]


async def test_refresh_on_401_and_expiry(cfg) -> None:
    svc, fake, _ = make_service(cfg)
    connect(svc)
    fake.fail["/me"] = [401]
    assert (await svc.spotify.me())["display_name"] == "Host"
    assert len(fake.token_forms) == 1 and svc.spotify.tokens["access_token"] == "AT1"
    svc.spotify.tokens["expires_at"] = time.time() + 30  # within 60 s: refresh first
    await svc.spotify.me()
    assert len(fake.token_forms) == 2
    # Revoked refresh token: disconnected, error surfaces.
    svc.spotify.tokens["expires_at"] = 0
    fake.fail["/api/token"] = [400]
    with pytest.raises(SpotifyError):
        await svc.spotify.me()
    assert not svc.spotify.connected and svc.state()["spotify"]["error"]


async def test_loop_survives_errors(cfg) -> None:
    cfg.jukebox.poll_active_s = cfg.jukebox.poll_idle_s = 0.02
    svc, fake, _ = make_service(cfg)
    connect(svc)
    svc.BACKOFF_MIN_S = 0.02
    fake.fail["/me/player/devices"] = [500, 500]
    fake.fail["/me/player"] = [502]
    await svc.start()
    try:
        await wait_for(lambda: svc.error == "nope")
        await wait_for(lambda: svc.error is None and svc.device_found)
        bob = svc.guest_for(svc.join("Bob", None)["token"])
        await svc.add(bob, f"spotify:track:{tid(1)}")
        await wait_for(lambda: bool(fake.play_calls()))
    finally:
        await svc.stop()


# ----- review regressions -----


async def test_racing_votes_skip_one_track(cfg) -> None:
    svc, fake, _ = make_service(cfg)
    connect(svc)
    gs = [svc.guest_for(svc.join(f"G{i}", None)["token"]) for i in range(4)]
    req = gs[0]
    svc.settings["per_guest_limit"] = 5
    for n in (1, 2, 3):
        await svc.add(req, f"spotify:track:{tid(n)}")
    await svc._tick()
    svc.settings["skip_vote_ratio"] = 0.5  # 4 active => 2 votes
    svc.votes.add(gs[1]["id"])
    a, b = await asyncio.gather(svc.vote_skip(gs[2]), svc.vote_skip(gs[3]))
    assert [a["skipped"], b["skipped"]].count(True) == 1
    assert len(fake.play_calls()) == 2  # only track 2 started
    assert svc.current["uri"] == f"spotify:track:{tid(2)}" and len(svc.queue) == 1


async def test_admin_double_tap_skips_once(cfg) -> None:
    svc, fake, _ = make_service(cfg)
    connect(svc)
    svc.SKIP_DEBOUNCE_S = 1.5
    host = svc.guest_for(svc.join("Host", None)["token"])
    for n in (1, 2, 3):
        await svc.add(host, f"spotify:track:{tid(n)}")
    await svc._tick()
    assert await svc.skip() is True
    assert await svc.skip() is False  # second tap right after
    assert await svc.skip(expect_uri=f"spotify:track:{tid(1)}") is False  # stale target
    assert len(fake.play_calls()) == 2


async def test_late_poll_after_end_advances(cfg) -> None:
    svc, fake, _ = make_service(cfg)
    connect(svc)
    bob = svc.guest_for(svc.join("Bob", None)["token"])
    await svc.add(bob, f"spotify:track:{tid(1)}")
    await svc.add(bob, f"spotify:track:{tid(2)}")
    await svc._tick()
    fake.set_playing(1, 180_000 - 100, playing=False)  # stopped, progress still at the end
    await svc._tick()
    assert fake.play_calls()[-1] == {"uris": [f"spotify:track:{tid(2)}"]}


async def test_queue_change_during_play_call(cfg) -> None:
    svc, fake, _ = make_service(cfg)
    connect(svc)
    host = svc.guest_for(svc.join("Host", None)["token"])
    await svc.add(host, f"spotify:track:{tid(1)}")
    await svc.add(host, f"spotify:track:{tid(2)}")
    real_play = svc.spotify.play

    async def play(dev, uris=None, context_uri=None):
        svc.clear()  # host clears while Spotify is being asked to play
        await real_play(dev, uris=uris, context_uri=context_uri)

    svc.spotify.play = play  # type: ignore[method-assign]
    await svc._tick()
    assert svc.current["uri"] == f"spotify:track:{tid(1)}" and svc.queue == []
    assert svc.history[0]["title"] == "Song 1"
    await svc.logout()  # the music stops with the login
    assert fake.playback["is_playing"] is False and not svc.spotify.connected


async def test_paused_fallback_not_cut_and_other_device_left_alone(cfg) -> None:
    svc, fake, _ = make_service(cfg)
    connect(svc)
    bob = svc.guest_for(svc.join("Bob", None)["token"])
    svc.update_settings({"fallback_uri": f"spotify:playlist:{ID}"})
    await svc._tick()
    await svc.add(bob, f"spotify:track:{tid(1)}")
    fake.playback.update(progress_ms=60_000, is_playing=False)  # paused in the Spotify app
    await svc._tick()
    assert len(fake.play_calls()) == 1
    # Another device of the account actively playing: hands off.
    fake.playback = {
        "device": {"id": "phone", "name": "Phone"},
        "item": track(5),
        "is_playing": True,
        "progress_ms": 1000,
    }
    await svc._tick()
    assert len(fake.play_calls()) == 1 and "Phone" in svc.state()["spotify"]["error"]
    fake.playback["is_playing"] = False  # paused elsewhere: the party takes over
    await svc._tick()
    assert fake.play_calls()[-1] == {"uris": [f"spotify:track:{tid(1)}"]}
    assert svc.state()["spotify"]["error"] is None


async def test_premium_notice_survives_ticks(cfg) -> None:
    svc, fake, _ = make_service(cfg)
    fake.product = "free"
    svc.set_spotify("a" * 32, None)
    state = parse_qs(urlparse(svc.login_url()).query)["state"][0]
    await svc.finish_login("c", state)
    await svc._tick()
    assert "Premium" in svc.state()["spotify"]["error"]


def test_backup_strips_jukebox_secrets(cfg) -> None:
    import io
    import sqlite3
    import zipfile

    from spookhub import backup

    svc, _, _ = make_service(cfg)
    connect(svc)
    a = svc.join("Host", None)
    svc.settings["volume"] = 42
    svc._save()
    raw = zipfile.ZipFile(io.BytesIO(backup.make_backup(cfg.paths.db_path))).read("spookhub.db")
    assert b"RT0" not in raw and svc.guests[a["guest"]["id"]]["token_hash"].encode() not in raw
    out = cfg.paths.data_dir / "check.db"
    out.write_bytes(raw)
    con = sqlite3.connect(out)
    data = json.loads(con.execute("SELECT value FROM setting WHERE key='jukebox'").fetchone()[0])
    con.close()
    assert data["tokens"] is None and data["settings"]["volume"] == 42
    assert data["guests"][0]["name"] == "Host" and "token_hash" not in data["guests"][0]


async def test_audio_device_file_and_sink_restart(cfg, tmp_path) -> None:
    svc, fake, _ = make_service(cfg)
    svc.jc.audio_device_file = tmp_path / "missing" / "device"
    assert svc.write_audio_device("alsa/spook_0_3") is False  # dev: no directory, no error
    path = tmp_path / "device"
    svc.jc.audio_device_file = path
    assert svc.write_audio_device(None) is True and path.read_text() == "auto\n"
    assert svc.write_audio_device(None) is False  # unchanged
    connect(svc)
    bob = svc.guest_for(svc.join("Bob", None)["token"])
    await svc.add(bob, f"spotify:track:{tid(1)}")
    await svc._tick()
    n = len(fake.calls)
    await svc.set_audio_device("alsa/spook_0_3")
    assert path.read_text() == "alsa/spook_0_3\n"
    assert fake.paths()[n:] == ["PUT /me/player/pause", "PUT /me/player/play"]


async def test_review_edge_cases(cfg) -> None:
    svc, fake, _ = make_service(cfg)
    connect(svc)
    bob = svc.guest_for(svc.join("Bob", None)["token"])
    # Track picked by hand in the Spotify app mid-song, queue empty: keeps playing.
    await svc.add(bob, f"spotify:track:{tid(1)}")
    await svc._tick()
    fake.set_playing(1, 30_000)
    await svc._tick()
    fake.set_playing(8, 1_000)
    await svc._tick()
    assert "PUT /me/player/pause" not in fake.paths()
    # Host skip with an empty queue: the skipped track stays hidden.
    await svc.add(bob, f"spotify:track:{tid(2)}")
    fake.set_playing(8, 179_900)
    await svc._tick()
    fake.set_playing(2, 5_000)
    await svc._tick()
    await svc.skip()
    await svc._tick()
    assert svc.state()["now"] is None
    # Playback moved to another (paused) device early in a song: not a dead speaker.
    await svc.add(bob, f"spotify:track:{tid(3)}")
    await svc._tick()
    fake.playback = {
        "device": {"id": "phone", "name": "Phone"},
        "item": track(3),
        "is_playing": False,
        "progress_ms": 1_000,
    }
    await svc._tick()
    assert svc._lost == 0 and not any(x["uri"].endswith(tid(3)) for x in svc.queue)


async def test_lost_error_clears_when_song_removed(cfg) -> None:
    svc, fake, _ = make_service(cfg)
    connect(svc)
    host = svc.guest_for(svc.join("Host", None)["token"])
    await svc.add(host, f"spotify:track:{tid(1)}")
    await svc._tick()
    fake.playback = None
    await svc._tick()
    assert svc.state()["spotify"]["error"]
    svc.clear()
    await svc._tick()
    assert svc.state()["spotify"]["error"] is None


async def test_sink_restart_failure_shows_pause(cfg, tmp_path) -> None:
    svc, fake, _ = make_service(cfg)
    svc.jc.audio_device_file = tmp_path / "device"
    connect(svc)
    bob = svc.guest_for(svc.join("Bob", None)["token"])
    await svc.add(bob, f"spotify:track:{tid(1)}")
    await svc._tick()
    fake.fail["/me/player/play"] = [500]
    await svc.set_audio_device("alsa/spook_0_3")
    assert svc.paused == "admin"
