"""Domain + persistence models."""

from __future__ import annotations

import enum
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

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
    scene = "scene"


class BoardSelection(BaseModel):
    """macs and groups add up; groups resolve to their members when the profile loads."""

    all: bool = True
    macs: list[str] = []
    groups: list[int] = []


class CueTarget(BaseModel):
    """all=True means 'all selected boards' of the profile.

    segs maps a board MAC to WLED segment ids: on that board the cue drives only those
    segments instead of the whole board. None = no segment targeting (whole boards only).
    """

    all: bool = True
    macs: list[str] = []
    groups: list[int] = []
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


class BoardGroup(SQLModel, table=True):
    """A room: named set of boards. Members live in board_group_member."""

    __tablename__ = "board_group"
    id: int | None = SQLField(default=None, primary_key=True)
    name: str
    color: str = SQLField(default="#7c3aed", sa_column_kwargs={"server_default": "#7c3aed"})
    sort: int = SQLField(default=0, sa_column_kwargs={"server_default": "0"})


class BoardGroupMember(SQLModel, table=True):
    __tablename__ = "board_group_member"
    group_id: int = SQLField(foreign_key="board_group.id", primary_key=True)
    # No FK to board: a removed board drops out of its groups in BoardManager.remove.
    mac: str = SQLField(primary_key=True)


class Media(SQLModel, table=True):
    id: int | None = SQLField(default=None, primary_key=True)
    filename: str
    path: str
    size: int = 0
    duration_s: float = 0.0
    width: int = 0
    height: int = 0
    codec: str = ""
    # Container rotation, clockwise (width/height are the coded frame). None = not probed yet
    # (rows from before the column existed); backfilled at startup.
    rotation: int | None = None
    # 'video' | 'audio'; audio rows have no picture (no thumbs, transcode or black check).
    kind: str = SQLField(default="video", sa_column_kwargs={"server_default": "video"})
    created_at: datetime = SQLField(default_factory=utcnow)
    # Per-video framing override; plain id (no FK), cleared when the display profile goes.
    display_profile_id: int | None = None


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
    display_profile_id: int | None = None
    # SceneSpec for type=scene, None otherwise.
    scene: dict[str, Any] | None = SQLField(default=None, sa_column=Column(JSON, nullable=True))
    # AmbientSpec: sound looped on the ambient player while the profile is active.
    ambient: dict[str, Any] | None = SQLField(default=None, sa_column=Column(JSON, nullable=True))
    # Idle -> main fade (picture + sound), 0 = hard cut.
    fade_ms: int = SQLField(default=400, sa_column_kwargs={"server_default": "400"})
    # Light profiles: loop length of the idle cue track; None = no idle loop.
    idle_length_s: float | None = None


class DisplayProfile(SQLModel, table=True):
    id: int | None = SQLField(default=None, primary_key=True)
    name: str = SQLField(sa_column_kwargs={"unique": True})
    geometry: dict[str, Any] = SQLField(
        default_factory=dict, sa_column=Column(JSON, nullable=False, server_default="{}")
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
    # 'light' | 'sound'. Sound cues ignore target/state; `sound` holds a SoundSpec.
    kind: str = SQLField(default="light", sa_column_kwargs={"server_default": "light"})
    sound: dict[str, Any] | None = SQLField(default=None, sa_column=Column(JSON, nullable=True))
    # 'main' (main video timeline) | 'idle' (loops with the idle clip / idle_length_s).
    track: str = SQLField(default="main", sa_column_kwargs={"server_default": "main"})


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


# ---------- Display geometry ----------

Unit = Annotated[float, Field(ge=0, le=1, allow_inf_nan=False)]
Half = Annotated[float, Field(ge=0, le=0.5, allow_inf_nan=False)]
Offset = Annotated[float, Field(ge=-0.5, le=0.5, allow_inf_nan=False)]
ImageAdj = Annotated[int, Field(ge=-100, le=100)]


class CropRect(BaseModel):
    """Fractions of the source as displayed (after rotation)."""

    x: Unit = 0.0
    y: Unit = 0.0
    w: Unit = 1.0
    h: Unit = 1.0

    @model_validator(mode="after")
    def _inside(self) -> CropRect:
        if self.w <= 0 or self.h <= 0:
            raise ValueError("crop must have a size")
        if self.x + self.w > 1 + 1e-6 or self.y + self.h > 1 + 1e-6:
            raise ValueError("crop must lie inside the frame")
        return self


class MaskRect(BaseModel):
    top: Half = 0.0
    right: Half = 0.0
    bottom: Half = 0.0
    left: Half = 0.0
    feather: Half = 0.0


Corner = tuple[Offset, Offset]
ZERO_CORNERS: list[Corner] = [(0.0, 0.0)] * 4


class DisplayGeometry(BaseModel):
    """How everything mpv plays is framed on the projector. Default = untouched picture."""

    zoom: float = Field(default=0.0, ge=-2, le=2, allow_inf_nan=False)
    pan_x: float = Field(default=0.0, ge=-1, le=1, allow_inf_nan=False)
    pan_y: float = Field(default=0.0, ge=-1, le=1, allow_inf_nan=False)
    scale_x: float = Field(default=1.0, ge=0.25, le=4, allow_inf_nan=False)
    scale_y: float = Field(default=1.0, ge=0.25, le=4, allow_inf_nan=False)
    align_x: float = Field(default=0.0, ge=-1, le=1, allow_inf_nan=False)
    align_y: float = Field(default=0.0, ge=-1, le=1, allow_inf_nan=False)
    rotate: Literal[0, 90, 180, 270] = 0
    aspect: float | None = Field(default=None, ge=0.2, le=10, allow_inf_nan=False)
    crop: CropRect = CropRect()
    brightness: ImageAdj = 0
    contrast: ImageAdj = 0
    gamma: ImageAdj = 0
    saturation: ImageAdj = 0
    # TL, TR, BR, BL offsets as fractions of the output size.
    corners: list[Corner] = Field(
        default_factory=lambda: list(ZERO_CORNERS), min_length=4, max_length=4
    )
    mask: MaskRect = MaskRect()
    flip_h: bool = False
    flip_v: bool = False


class DisplayProfileIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    geometry: DisplayGeometry = DisplayGeometry()


class DisplayProfilePatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    geometry: DisplayGeometry | None = None


class DisplayProfileOut(DisplayProfileIn):
    id: int


# ---------- API schemas ----------


HEX_COLOR = r"^#[0-9a-fA-F]{6}$"


class Marker(BaseModel):
    id: str = Field(min_length=1, max_length=64)
    t: float = Field(ge=0, allow_inf_nan=False)
    label: str = Field(default="", max_length=200)
    color: str | None = Field(default=None, pattern=HEX_COLOR)


class SceneItem(BaseModel):
    profile_id: int
    weight: float = Field(default=1.0, gt=0, le=1000, allow_inf_nan=False)


class SceneSpec(BaseModel):
    items: list[SceneItem] = Field(default=[], max_length=200)
    order: Literal["sequential", "shuffle", "weighted"] = "shuffle"
    idle_profile_id: int | None = None
    chain: bool = False
    light_hold_s: float = Field(default=10.0, gt=0, le=3600, allow_inf_nan=False)


class AmbientSpec(BaseModel):
    media_id: int
    volume: float = Field(default=1.0, ge=0, le=1.3, allow_inf_nan=False)
    # Fraction of `volume` while the main video plays.
    duck_to: float = Field(default=0.3, ge=0, le=1, allow_inf_nan=False)


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
    display_profile_id: int | None = None
    scene: SceneSpec | None = None
    ambient: AmbientSpec | None = None
    fade_ms: int = Field(default=400, ge=0, le=10000)
    idle_length_s: float | None = Field(default=None, ge=0.5, le=3600, allow_inf_nan=False)

    @model_validator(mode="after")
    def _video_needs_media(self) -> ProfileIn:
        return self


class SoundSpec(BaseModel):
    media_id: int
    volume: float = Field(default=1.0, ge=0, le=1.3, allow_inf_nan=False)
    fade_in_ms: int = Field(default=0, ge=0, le=60000)
    fade_out_ms: int = Field(default=0, ge=0, le=60000)
    loop: bool = False


CueKind = Literal["light", "sound"]
CueTrack = Literal["main", "idle"]


class CueIn(BaseModel):
    start_s: float = Field(ge=0, allow_inf_nan=False)
    duration_s: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    target: CueTarget = CueTarget()
    # Required for light cues (validator); sound cues ignore it.
    state: LightState = LightState()
    transition_ms: int | None = Field(default=None, ge=0)
    label: str | None = Field(default=None, max_length=200)
    color: str | None = Field(default=None, pattern=HEX_COLOR)
    kind: CueKind = "light"
    sound: SoundSpec | None = None
    track: CueTrack = "main"

    @model_validator(mode="after")
    def _kind_fields(self) -> CueIn:
        if self.kind == "sound":
            if self.sound is None:
                raise ValueError("sound cue needs a sound")
        else:
            if "state" not in self.model_fields_set:
                raise ValueError("light cue needs a state")
            self.sound = None
        return self


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
    # Global default display profile ("Projector setup"); None = identity geometry.
    display_profile_id: int | None = None


# ---------- Board groups ----------


GROUP_KEY = "group:"


class GroupIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    color: str = Field(default="#7c3aed", pattern=HEX_COLOR)
    macs: list[str] = Field(default=[], max_length=500)


class GroupOut(GroupIn):
    id: int
    sort: int


# ---------- Moments ----------


class Moment(SQLModel, table=True):
    """A button on /moments: plays one variant once, then restores what ran before."""

    id: int | None = SQLField(default=None, primary_key=True)
    name: str
    icon: str = ""
    color: str = "#7c3aed"
    sort: int = 0
    cooldown_s: float = 0.0
    busy: str = "ignore"  # 'ignore' | 'interrupt' | 'queue'
    order: str = "shuffle"  # 'shuffle' | 'weighted'
    variants: list[dict[str, Any]] = SQLField(
        default_factory=list, sa_column=Column(JSON, nullable=False, server_default="[]")
    )


class MomentSound(BaseModel):
    media_id: int
    volume: float = Field(default=1.0, ge=0, le=1.3, allow_inf_nan=False)
    fade_in_ms: int = Field(default=0, ge=0, le=60000)
    fade_out_ms: int = Field(default=0, ge=0, le=60000)


class MomentVariant(BaseModel):
    video_profile_id: int | None = None
    sound: MomentSound | None = None
    light_profile_id: int | None = None
    ambience: Literal["keep", "mute", "replace"] = "keep"
    ambience_media_id: int | None = None
    weight: float = Field(default=1.0, gt=0, le=1000, allow_inf_nan=False)

    @model_validator(mode="after")
    def _parts(self) -> MomentVariant:
        if self.video_profile_id is None and self.sound is None and self.light_profile_id is None:
            raise ValueError("a variant needs a video, a sound or a light part")
        if self.ambience == "replace":
            if self.ambience_media_id is None:
                raise ValueError("ambience 'replace' needs ambience_media_id")
        else:
            self.ambience_media_id = None
        return self


class MomentIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    icon: str = Field(default="", max_length=32)
    color: str = Field(default="#7c3aed", pattern=HEX_COLOR)
    cooldown_s: float = Field(default=0.0, ge=0, le=3600, allow_inf_nan=False)
    busy: Literal["ignore", "interrupt", "queue"] = "ignore"
    order: Literal["shuffle", "weighted"] = "shuffle"
    variants: list[MomentVariant] = Field(min_length=1, max_length=50)


class MomentOut(MomentIn):
    id: int
    sort: int
    # Deleting a profile strips its parts; a moment can end up with no variants until edited.
    variants: list[MomentVariant] = Field(max_length=50)
