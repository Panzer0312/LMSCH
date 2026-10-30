"""Configuration: one TOML file with defaults, overridable by SPOOKHUB_* env vars.

Env nesting uses double underscores, e.g. SPOOKHUB_MPV__VO=x11.
"""

from __future__ import annotations

import os
import tomllib
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import BaseModel
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

DEFAULT_CONFIG_PATH = Path("/etc/spookhub/config.toml")


class ServerCfg(BaseModel):
    host: str = "0.0.0.0"
    port: int = 8080
    log_level: str = "info"
    frontend_dir: Path = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"


class PathsCfg(BaseModel):
    data_dir: Path = Path("/var/lib/spookhub")

    @property
    def db_path(self) -> Path:
        return self.data_dir / "spookhub.db"

    @property
    def media_dir(self) -> Path:
        return self.data_dir / "media"


class MpvCfg(BaseModel):
    enabled: bool = True
    binary: str = "mpv"
    # Debian minimal has no X/Wayland: render straight to KMS/DRM.
    vo: str = "gpu"
    gpu_context: str | None = "drm"
    hwdec: str = "auto-safe"
    ao: str | None = "alsa"
    extra_args: list[str] = []
    socket_path: Path = Path("/run/spookhub/mpv.sock")
    restart_delay_s: float = 1.0


class TimingCfg(BaseModel):
    health_interval_s: float = 3.0
    health_timeout_s: float = 1.5
    degraded_latency_ms: float = 250.0
    offline_after_failures: int = 3
    cue_timeout_s: float = 0.3
    command_timeout_s: float = 2.0
    backoff_max_s: float = 15.0
    scheduler_tick_s: float = 0.01
    latency_comp_cap_ms: float = 150.0
    scan_concurrency: int = 64
    scan_timeout_s: float = 0.8


class JukeboxCfg(BaseModel):
    enabled: bool = True
    # Spotify Connect name of the librespot speaker on the hub (raspotify LIBRESPOT_NAME).
    device_name: str = "Spookhub"
    # None: http://127.0.0.1:<server.port>/api/jukebox/spotify/callback (Spotify allows plain
    # http only on loopback).
    redirect_uri: str | None = None
    poll_active_s: float = 3.0
    poll_idle_s: float = 15.0
    accounts_url: str = "https://accounts.spotify.com"
    api_url: str = "https://api.spotify.com/v1"


class Config(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SPOOKHUB_", env_nested_delimiter="__")

    server: ServerCfg = ServerCfg()
    paths: PathsCfg = PathsCfg()
    mpv: MpvCfg = MpvCfg()
    timing: TimingCfg = TimingCfg()
    jukebox: JukeboxCfg = JukeboxCfg()
    # Disable discovery for tests / sandboxed dev.
    mdns_enabled: bool = True

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return (init_settings, env_settings, _TomlSource(settings_cls))


class _TomlSource(PydanticBaseSettingsSource):
    def get_field_value(self, field: Any, field_name: str) -> tuple[Any, str, bool]:
        return None, field_name, False

    def __call__(self) -> dict[str, Any]:
        path = Path(os.environ.get("SPOOKHUB_CONFIG", DEFAULT_CONFIG_PATH))
        if path.is_file():
            with path.open("rb") as f:
                return tomllib.load(f)
        return {}


@lru_cache
def get_config() -> Config:
    return Config()
