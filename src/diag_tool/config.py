"""Anwendungskonfiguration — pydantic-settings + TOML.

Suchreihenfolge:
1. Umgebungsvariablen mit Präfix ``DIAG_``
2. ``$XDG_CONFIG_HOME/diag-tool/config.toml`` (bzw. ``~/.config/diag-tool/config.toml``)
3. Defaults
"""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, IPvAnyAddress
from pydantic_settings import BaseSettings, SettingsConfigDict


def _config_dir() -> Path:
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg) if xdg else Path.home() / ".config"
    return base / "diag-tool"


def _data_dir() -> Path:
    xdg = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg) if xdg else Path.home() / ".local" / "share"
    return base / "diag-tool"


class MulticastEndpoint(BaseModel):
    """Ein Multicast-Endpunkt (Gruppe + Port + Interface)."""

    group: IPvAnyAddress = Field(description="Multicast-Gruppen-Adresse")
    port: int = Field(ge=1, le=65535)
    interface: str = Field(default="0.0.0.0", description="Bind-Interface")
    ttl: int = Field(default=1, ge=1, le=255)


class NetworkConfig(BaseModel):
    """Zwei Multicast-Schnittstellen: passiver Betrieb + Diagnose-Command."""

    system: MulticastEndpoint = Field(
        default_factory=lambda: MulticastEndpoint(group="239.10.0.1", port=30001)  # type: ignore[arg-type]
    )
    diagnose: MulticastEndpoint = Field(
        default_factory=lambda: MulticastEndpoint(group="239.10.0.2", port=30002)  # type: ignore[arg-type]
    )


class LoggingConfig(BaseModel):
    session_dir: Path = Field(default_factory=lambda: _data_dir() / "sessions")
    level: str = Field(default="INFO")


class AppConfig(BaseSettings):
    """Root-Konfiguration."""

    model_config = SettingsConfigDict(
        env_prefix="DIAG_",
        env_nested_delimiter="__",
        extra="ignore",
    )

    network: NetworkConfig = Field(default_factory=NetworkConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)

    @classmethod
    def load(cls) -> AppConfig:
        config_file = _config_dir() / "config.toml"
        data: dict[str, Any] = {}
        if config_file.is_file():
            with config_file.open("rb") as f:
                data = tomllib.load(f)
        return cls(**data)


CONFIG_DIR = _config_dir
DATA_DIR = _data_dir
