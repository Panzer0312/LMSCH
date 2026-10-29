"""Runtime settings persisted as one JSON row so the UI can edit them without a restart."""

from __future__ import annotations

import logging
from collections.abc import Callable

from pydantic import ValidationError

from spookhub import db
from spookhub.models import RuntimeSettings, Setting

log = logging.getLogger(__name__)

KEY = "runtime"


class SettingsStore:
    def __init__(self) -> None:
        self.value = RuntimeSettings()
        self.listeners: list[Callable[[RuntimeSettings, RuntimeSettings], None]] = []

    def load(self) -> RuntimeSettings:
        with db.session() as s:
            row = s.get(Setting, KEY)
            if row and isinstance(row.value, dict):
                try:
                    self.value = RuntimeSettings.model_validate(row.value)
                except ValidationError as e:
                    # A value stored before a limit existed must not stop the hub booting.
                    bad = {err["loc"][0] for err in e.errors() if err["loc"]}
                    log.warning("dropping invalid stored settings: %s", sorted(map(str, bad)))
                    clean = {k: v for k, v in row.value.items() if k not in bad}
                    self.value = RuntimeSettings.model_validate(clean)
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
