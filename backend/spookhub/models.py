"""Domain + persistence models."""

from __future__ import annotations

import enum
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator
from sqlalchemy import JSON, Column
from sqlmodel import Field as SQLField
from sqlmodel import SQLModel


def utcnow() -> datetime:
    return datetime.now(UTC)


# ---------- Light state ----------

RGB = tuple[int, int, int]


class LightState(BaseModel):
    """Either a preset or an explicit state. Unset explicit fields are left untouched."""

    preset: int | None = None
    on: bool | None = None
    bri: int | None = Field(default=None, ge=0, le=255)
    fx: int | None = Field(default=None, ge=0)
    pal: int | None = Field(default=None, ge=0)
    colors: list[RGB] | None = Field(default=None, max_length=3)
    sx: int | None = Field(default=None, ge=0, le=255)
    ix: int | None = Field(default=None, ge=0, le=255)
    transition_ms: int | None = Field(default=None, ge=0, le=65535 * 100)

    def merged(self, other: LightState | None) -> LightState:
        """Overlay `other` on self. A preset in `other` replaces everything; otherwise
        explicit fields win and a base preset stays as the starting point (expanded at send)."""
        if other is None:
            return self
        if other.preset is not None:
            return other
        data = self.model_dump(exclude_none=True)
        data.update(other.model_dump(exclude_none=True))
        return LightState(**data)


OFF_STATE = LightState(on=False, transition_ms=0)


class PlaybackMode(enum.StrEnum):
    once = "once"
    loop = "loop"
    idle_trigger = "idle_trigger"


class ProfileType(enum.StrEnum):
    light = "light"
    video = "video"


class BoardSelection(BaseModel):
    all: bool = True
    macs: list[str] = []


class CueTarget(BaseModel):
    """all=True means 'all selected boards' of the profile.

    segs maps a board MAC to WLED segment ids: on that board the cue drives only those
    segments instead of the whole board. None = no segment targeting (whole boards only).
    """

    all: bool = True
    macs: list[str] = []
    segs: dict[str, list[int]] | None = None


# ---------- Tables ----------


class Board(SQLModel, table=True):
    mac: str = SQLField(primary_key=True)
    ip: str
    name: str = ""
    adopted: bool = False
    firmware: str = ""
    led_count: int = 0
    effects: list[str] = SQLField(default_factory=list, sa_column=Column(JSON))
    palettes: list[str] = SQLField(default_factory=list, sa_column=Column(JSON))
    fxdata: list[str] = SQLField(
        default_factory=list, sa_column=Column(JSON, nullable=False, server_default="[]")
    )
    presets: dict[str, Any] = SQLField(default_factory=dict, sa_column=Column(JSON))
    # [{id, name, start, stop}] as last read from the board's /json/state "seg".
    segments: list[dict[str, Any]] = SQLField(
        default_factory=list, sa_column=Column(JSON, nullable=False, server_default="[]")
    )
    last_seen: datetime | None = None
    # Per-board fire-time trim in ms (positive = fire earlier), on top of measured latency.
    offset_ms: float = SQLField(default=0.0, sa_column_kwargs={"server_default": "0"})
    created_at: datetime = SQLField(default_factory=utcnow)


class Media(SQLModel, table=True):
    id: int | None = SQLField(default=None, primary_key=True)
    filename: str
    path: str
    size: int = 0
    duration_s: float = 0.0
    width: int = 0
    height: int = 0
    codec: str = ""
    created_at: datetime = SQLField(default_factory=utcnow)


class Profile(SQLModel, table=True):
    id: int | None = SQLField(default=None, primary_key=True)
    name: str
    type: ProfileType = ProfileType.light
    sort: int = 0
    selection: dict[str, Any] = SQLField(
        default_factory=lambda: BoardSelection().model_dump(), sa_column=Column(JSON)
    )
    base_state: dict[str, Any] = SQLField(default_factory=dict, sa_column=Column(JSON))
    overrides: dict[str, Any] = SQLField(default_factory=dict, sa_column=Column(JSON))
    media_id: int | None = SQLField(default=None, foreign_key="media.id")
    idle_media_id: int | None = SQLField(default=None, foreign_key="media.id")
    playback_mode: PlaybackMode = PlaybackMode.once
    cooldown_s: float = 0.0
    markers: list[dict[str, Any]] = SQLField(
        default_factory=list, sa_column=Column(JSON, nullable=False, server_default="[]")
    )


class Cue(SQLModel, table=True):
    id: int | None = SQLField(default=None, primary_key=True)
    profile_id: int = SQLField(foreign_key="profile.id", index=True)
    start_s: float
    duration_s: float | None = None
    target: dict[str, Any] = SQLField(
        default_factory=lambda: CueTarget().model_dump(), sa_column=Column(JSON)
    )
    state: dict[str, Any] = SQLField(default_factory=dict, sa_column=Column(JSON))
    transition_ms: int | None = None
    label: str | None = None
    color: str | None = None


class CueTemplate(SQLModel, table=True):
    id: int | None = SQLField(default=None, primary_key=True)
    name: str
    cues: list[dict[str, Any]] = SQLField(
        default_factory=list, sa_column=Column(JSON, nullable=False, server_default="[]")
    )
    created_at: datetime = SQLField(default_factory=utcnow)


class Setting(SQLModel, table=True):
    key: str = SQLField(primary_key=True)
    value: Any = SQLField(sa_column=Column(JSON))


class EventLog(SQLModel, table=True):
    """Persistent run log (written in batches by EventWriter)."""

    id: int | None = SQLField(default=None, primary_key=True)
    ts: float = SQLField(index=True)
    level: str
    kind: str = "info"
    message: str
    data: Any = SQLField(default=None, sa_column=Column(JSON))


# ---------- API schemas ----------


HEX_COLOR = r"^#[0-9a-fA-F]{6}$"


class Marker(BaseModel):
    id: str = Field(min_length=1, max_length=64)
    t: float = Field(ge=0, allow_inf_nan=False)
    label: str = Field(default="", max_length=200)
    color: str | None = Field(default=None, pattern=HEX_COLOR)


class ProfileIn(BaseModel):
    name: str
    type: ProfileType = ProfileType.light
    selection: BoardSelection = BoardSelection()
    base_state: LightState = LightState(on=True, bri=128)
    overrides: dict[str, LightState] = {}
    media_id: int | None = None
    idle_media_id: int | None = None
    playback_mode: PlaybackMode = PlaybackMode.once
    cooldown_s: float = Field(default=0.0, ge=0)
    # None = leave stored markers untouched (they have their own endpoint).
    markers: list[Marker] | None = None

    @model_validator(mode="after")
    def _video_needs_media(self) -> ProfileIn:
        return self


class CueIn(BaseModel):
    start_s: float = Field(ge=0, allow_inf_nan=False)
    duration_s: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    target: CueTarget = CueTarget()
    state: LightState
    transition_ms: int | None = Field(default=None, ge=0)
    label: str | None = Field(default=None, max_length=200)
    color: str | None = Field(default=None, pattern=HEX_COLOR)


class TemplateCue(BaseModel):
    offset_s: float = Field(ge=0, allow_inf_nan=False)
    duration_s: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    lane: Literal["all", "first", "same"] = "all"
    state: LightState
    transition_ms: int | None = Field(default=None, ge=0)
    label: str | None = Field(default=None, max_length=200)
    color: str | None = Field(default=None, pattern=HEX_COLOR)


class TemplateIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    cues: list[TemplateCue] = Field(min_length=1, max_length=500)


class TemplateOut(TemplateIn):
    id: int
    created_at: datetime


class CueOut(CueIn):
    id: int


class ProfileOut(ProfileIn):
    id: int
    sort: int
    markers: list[Marker] = []  # type: ignore[assignment]
    cues: list[CueOut] = []


class RuntimeSettings(BaseModel):
    """Settings editable from the UI, stored in the DB."""

    sync_offset_ms: float = Field(default=0.0, ge=-2000, le=2000, allow_inf_nan=False)
    master_brightness: float = Field(default=1.0, ge=0, le=1)
    health_interval_s: float = Field(default=3.0, ge=0.5, le=60)
    default_transition_ms: int = Field(default=0, ge=0)
    log_level: str = "info"
    mpv_vo: str | None = None
    mpv_gpu_context: str | None = None
    mpv_drm_connector: str | None = None
    mpv_drm_mode: str | None = None
    mpv_audio_device: str | None = None
    master_volume: float = Field(default=100, ge=0, le=130)
    volume_cap: float = Field(default=100, ge=0, le=130)
    brightness_cap: float = Field(default=1.0, ge=0, le=1)
    audio_delay_s: float = Field(default=0.0, ge=-5, le=5)
    show_lock: bool = False
    active_profile_id: int | None = None
