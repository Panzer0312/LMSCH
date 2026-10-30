"""Fair play order for the jukebox queue (pure).

Entry: {id, uri, title, artist, album, art_url, duration_ms, explicit, guest_id, added_seq,
bumped_seq | None}.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any


def ordered(entries: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Bumped entries first (latest bump first), then round-robin across guests.

    An entry's round is the number of earlier (by added_seq) pending entries of the same guest,
    so a newcomer's first song goes ahead of a heavy user's second.
    """
    items = list(entries)
    bumped = [e for e in items if e.get("bumped_seq") is not None]
    # "Play next" means next: the most recent bump wins.
    bumped.sort(key=lambda e: e["bumped_seq"], reverse=True)
    rest = sorted((e for e in items if e.get("bumped_seq") is None), key=lambda e: e["added_seq"])
    rounds: dict[str, int] = {}
    keyed: list[tuple[int, int, dict[str, Any]]] = []
    for e in rest:
        r = rounds.get(e["guest_id"], 0)
        rounds[e["guest_id"]] = r + 1
        keyed.append((r, e["added_seq"], e))
    keyed.sort(key=lambda k: (k[0], k[1]))
    return bumped + [e for _, _, e in keyed]
