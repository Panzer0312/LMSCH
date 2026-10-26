"""Runtime settings persisted as one JSON row so the UI can edit them without a restart."""

from __future__ import annotations

from collections.abc import Callable

from spookhub import db
from spookhub.models import RuntimeSettings, Setting

KEY = "runtime"


class SettingsStore:
    def __init__(self) -> None:
        self.value = RuntimeSettings()
        self.listeners: list[Callable[[RuntimeSettings, RuntimeSettings], None]] = []

    def load(self) -> RuntimeSettings:
        with db.session() as s:
            row = s.get(Setting, KEY)
            if row and isinstance(row.value, dict):
                self.value = RuntimeSettings.model_validate(row.value)
        return self.value

    def update(self, **changes: object) -> RuntimeSettings:
        old = self.value
        new = RuntimeSettings.model_validate({**old.model_dump(), **changes})
        with db.session() as s:
            row = s.get(Setting, KEY) or Setting(key=KEY, value={})
            row.value = new.model_dump()
            s.add(row)
            s.commit()
        self.value = new
        for fn in self.listeners:
            fn(old, new)
        return new
