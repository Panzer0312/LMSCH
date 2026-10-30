"""Playlist picker + scene runner state (no hub needed)."""

import random
from collections import Counter

import pytest

from spookhub.models import SceneItem, SceneSpec
from spookhub.show.picker import Picker
from spookhub.show.scene import SceneRunner

ITEMS = [(1, 1.0), (2, 1.0), (3, 1.0)]


def _run(p: Picker, items, n: int) -> list[int]:
    hist: list[int] = []
    for _ in range(n):
        pick = p.next(items, hist)
        assert pick is not None
        hist.append(pick)
    return hist


@pytest.mark.parametrize("seed", range(20))
def test_shuffle_never_repeats_back_to_back(seed):
    """Spec 4.1 accept: 3 scares, 30 triggers, never the same twice in a row."""
    hist = _run(Picker("shuffle", random.Random(seed)), ITEMS, 30)
    assert all(a != b for a, b in zip(hist, hist[1:], strict=False))
    # Bag semantics: every round of 3 uses each item once.
    for i in range(0, 30, 3):
        assert sorted(hist[i : i + 3]) == [1, 2, 3]


def test_shuffle_is_seeded():
    a = _run(Picker("shuffle", random.Random(7)), ITEMS, 12)
    b = _run(Picker("shuffle", random.Random(7)), ITEMS, 12)
    assert a == b


def test_shuffle_follows_item_changes():
    p = Picker("shuffle", random.Random(1))
    first = p.next(ITEMS, [])
    assert p.next([(9, 1.0)], [first]) == 9  # stale bag entries dropped
    assert p.next([(9, 1.0)], [9]) == 9  # single item may repeat


def test_sequential_cycles_and_restarts():
    p = Picker("sequential")
    assert _run(p, ITEMS, 7) == [1, 2, 3, 1, 2, 3, 1]
    assert p.next(ITEMS, [42]) == 1  # last played not in the list: start over


def test_weighted_respects_weights_without_repeat():
    p = Picker("weighted", random.Random(3))
    items = [(1, 8.0), (2, 1.0), (3, 1.0)]
    hist = _run(p, items, 600)
    assert all(a != b for a, b in zip(hist, hist[1:], strict=False))
    c = Counter(hist)
    assert c[1] > c[2] and c[1] > c[3]


def test_weighted_single_item_repeats():
    assert _run(Picker("weighted", random.Random(0)), [(5, 1.0)], 3) == [5, 5, 5]


def test_empty_items():
    for order in ("sequential", "shuffle", "weighted"):
        assert Picker(order).next([], []) is None  # type: ignore[arg-type]
        assert Picker(order).preview([], []) == []  # type: ignore[arg-type]


@pytest.mark.parametrize("order", ["shuffle", "weighted", "sequential"])
def test_preview_does_not_mutate(order):
    p = Picker(order, random.Random(11))
    p.next(ITEMS, [])  # half-used bag
    hist = [1]
    preview = p.preview(ITEMS, hist, 10)
    assert len(preview) == 10
    assert p.preview(ITEMS, hist, 10) == preview  # same state twice
    # And the real picks then match the preview.
    real = []
    for _ in range(10):
        nxt = p.next(ITEMS, hist + real)
        assert nxt is not None
        real.append(nxt)
    assert real == preview


def _spec(**kw) -> SceneSpec:
    items = [SceneItem(profile_id=i) for i in (1, 2, 3)]
    return SceneSpec(items=items, **kw)


def test_runner_take_pin_skip():
    r = SceneRunner(10, _spec(order="sequential"))
    assert r.state()["next_id"] == 1
    assert r.state()["upcoming"][:4] == [1, 2, 3, 1]
    assert r.take() == 1
    assert r.state()["next_id"] == 2
    r.pin(3)
    assert r.state()["upcoming"][:3] == [3, 2, 3]
    assert r.take() == 3  # pin wins once
    assert r.state()["pinned"] is None
    assert r.take() == 2  # queued next comes after
    r.skip()  # next was 3; skipped
    assert r.state()["next_id"] == 1
    assert r.state()["history"] == [1, 3, 2]
    with pytest.raises(ValueError):
        r.pin(99)
    r.pin(2)
    r.skip()  # skipping a pin just drops it
    assert r.state()["pinned"] is None and r.state()["next_id"] == 1


def test_runner_pin_avoids_double():
    r = SceneRunner(10, _spec(order="sequential"))
    r.pin(1)  # same as next_id
    assert r.take() == 1
    assert r.state()["next_id"] == 2


def test_runner_update_keeps_history():
    r = SceneRunner(10, _spec(order="sequential"))
    r.take()
    r.update(SceneSpec(items=[SceneItem(profile_id=2)], order="sequential"))
    assert r.state()["history"] == [1]
    assert r.state()["next_id"] == 2


def test_runner_shuffle_30_triggers_with_pins_never_double():
    r = SceneRunner(10, _spec(order="shuffle"), random.Random(5))
    picks = [r.take() for _ in range(30)]
    assert all(a != b for a, b in zip(picks, picks[1:], strict=False))
    assert len(r.state()["upcoming"]) == 10
