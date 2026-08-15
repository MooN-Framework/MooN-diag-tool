"""
Central application settings. Persisted via QSettings (INI file under
the user config directory). Reads fall back to defaults for missing
keys.

Kept separate from the UI layer so the core (non-Qt code) can access
it -- for non-GUI callers we return the raw dict.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any


@dataclass
class AppSettings:
    # Diag (JSON) channel
    diag_group: str = "239.10.0.2"
    diag_port: int = 6666
    # Operational (binary) channel
    op_group: str = "239.10.0.1"
    op_port: int = 5555
    # Common
    interface_ip: str = "127.0.0.1"
    # Paths
    session_log_dir: str = str(Path.home() / "diag-tool-sessions")
    rust_repo_path: str = ""          # location of the Rust node repo (for cargo build)
    # Empty by default -> the Test tab falls back to <this_repo>/tests/scenarios.
    scenarios_path: str = ""
    # Test mode
    test_mode: str = "simulated"      # "simulated" | "hardware"
    # Cross-compile
    target_triple: str = ""           # empty = host triple
    binary_name: str = "node"
    build_features: str = "diagnostic"  # required for the JSON diag channel
    # Hardware nodes: JSON-encoded list of HardwareNode dicts.
    hardware_nodes_json: str = "[]"

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def load_from_qsettings(qs) -> AppSettings:
    defaults = AppSettings()
    kwargs: dict[str, Any] = {}
    for k, default in defaults.as_dict().items():
        val = qs.value(k, default)
        # QSettings sometimes returns ints as strings.
        if isinstance(default, int) and not isinstance(val, int):
            try:
                val = int(val)
            except (TypeError, ValueError):
                val = default
        kwargs[k] = val
    return AppSettings(**kwargs)


def save_to_qsettings(qs, settings: AppSettings) -> None:
    for k, v in settings.as_dict().items():
        qs.setValue(k, v)
    qs.sync()
