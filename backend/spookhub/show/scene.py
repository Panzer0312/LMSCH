"""Scene playlist state: what played, what comes next, the pinned override."""

from __future__ import annotations

import random
from collections import deque
from typing import Any

from spookhub.models import SceneSpec
from spookhub.show.picker import Item, Picker

HISTORY = 50
UPCOMING = 10


class SceneRunner:
    def __init__(self, profile_id: int, spec: SceneSpec, rng: random.Random | None = None) -> None:
        self.profile_id = profile_id
        self.spec = spec
        self.picker = Picker(spec.order, rng)
        self.history: deque[int] = deque(maxlen=HISTORY)
        self.pinned: int | None = None
        self.next_id: int | None = None
        self._pick()

    def items(self) -> list[Item]:
        return [(i.profile_id, i.weight) for i in self.spec.items]

    def update(self, spec: SceneSpec) -> None:
        """Scene edited: keep history; re-pick when the order or the next item went away."""
        order_changed = spec.order != self.spec.order
        self.spec = spec
        ids = {i.profile_id for i in spec.items}
        if order_changed:
            self.picker = Picker(spec.order, self.picker.rng)
        if self.pinned not in ids:
            self.pinned = None
        if order_changed or self.next_id not in ids:
            self._pick()

    def _pick(self, skipped: int | None = None) -> None:
        hist = list(self.history) + ([skipped] if skipped is not None else [])
        self.next_id = self.picker.next(self.items(), hist)

    def take(self) -> int | None:
        """The item to play now. A pin wins once; the picked next stays queued behind it."""
        if self.pinned is not None:
            pid, self.pinned = self.pinned, None
            self.history.append(pid)
            if self.next_id == pid:  # would play twice in a row
                self._pick()
            return pid
        pid = self.next_id
        if pid is not None:
            self.history.append(pid)
            self._pick()
        return pid

    def skip(self) -> None:
        if self.pinned is not None:
            self.pinned = None
        elif self.next_id is not None:
            # The skipped item counts as just played for the pick, but not in the history.
            self._pick(skipped=self.next_id)

    def pin(self, profile_id: int | None) -> None:
        if profile_id is not None and profile_id not in {i for i, _ in self.items()}:
            raise ValueError("profile is not in this scene")
        self.pinned = profile_id

    def upcoming(self) -> list[int]:
        head = [p for p in (self.pinned, self.next_id) if p is not None]
        if self.next_id is None:
            return head[:UPCOMING]
        hist = list(self.history) + [self.next_id]
        return (head + self.picker.preview(self.items(), hist, UPCOMING))[:UPCOMING]

    def state(self) -> dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "next_id": self.next_id,
            "pinned": self.pinned,
            "history": list(self.history),
            "upcoming": self.upcoming(),
        }


IDLE_STATE: dict[str, Any] = {
    "profile_id": None,
    "next_id": None,
    "pinned": None,
    "history": [],
    "upcoming": [],
}
