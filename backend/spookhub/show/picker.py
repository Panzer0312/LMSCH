"""Pick the next item of a playlist: scenes now, moment variants later.

Items are (id, weight) pairs; history is the ids already played, oldest first.
"""

from __future__ import annotations

import random
from collections.abc import Sequence
from typing import Literal

Order = Literal["sequential", "shuffle", "weighted"]
Item = tuple[int, float]


class Picker:
    def __init__(self, order: Order, rng: random.Random | None = None) -> None:
        self.order: Order = order
        self.rng = rng or random.Random()
        # Shuffle bag: drawn from the end; refilled (reshuffled) when empty.
        self._bag: list[int] = []

    def next(self, items: Sequence[Item], history: Sequence[int]) -> int | None:
        ids = list(dict.fromkeys(i for i, _ in items))
        if not ids:
            return None
        last = history[-1] if history else None
        if self.order == "sequential":
            return ids[(ids.index(last) + 1) % len(ids)] if last in ids else ids[0]
        if self.order == "weighted":
            return self._weighted(items, last)
        return self._shuffle(ids, last)

    def preview(self, items: Sequence[Item], history: Sequence[int], n: int = 10) -> list[int]:
        """The next n picks, without touching this picker's RNG or bag."""
        clone = Picker(self.order, random.Random())
        clone.rng.setstate(self.rng.getstate())
        clone._bag = list(self._bag)
        hist = list(history)
        out: list[int] = []
        for _ in range(n):
            pick = clone.next(items, hist)
            if pick is None:
                break
            out.append(pick)
            hist.append(pick)
        return out

    def _shuffle(self, ids: list[int], last: int | None) -> int:
        # Items may have changed since the bag was filled.
        self._bag = [i for i in self._bag if i in ids]
        if not self._bag or self._bag == [last]:
            self._bag = list(ids)
            self.rng.shuffle(self._bag)
        if len(ids) > 1 and self._bag[-1] == last:
            # A fresh bag may start with the item that just played: never repeat back to back.
            j = self.rng.randrange(len(self._bag) - 1)
            self._bag[-1], self._bag[j] = self._bag[j], self._bag[-1]
        return self._bag.pop()

    def _weighted(self, items: Sequence[Item], last: int | None) -> int:
        pool = [(i, w) for i, w in items if i != last] if len({i for i, _ in items}) > 1 else []
        pool = pool or list(items)
        weights = [max(0.0, w) for _, w in pool]
        if sum(weights) <= 0:
            weights = [1.0] * len(pool)
        return self.rng.choices([i for i, _ in pool], weights=weights)[0]
