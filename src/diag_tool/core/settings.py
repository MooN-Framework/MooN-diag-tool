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
    # Network interface NAME (not IP) baked into a node's own config.toml
    # ([transport]/[diagnostic] interface=) for hardware deploys -- this is
    # what the Rust binary on the Pi binds its multicast sockets to.
    # Distinct from interface_ip above, which is this GUI machine's own
    # bind address for watching the sweep. Simulated mode always uses "lo"
    # (local subprocess nodes on loopback) regardless of this setting.
    node_network_interface: str = "eth0"
    # Paths
    session_log_dir: str = str(Path.home() / "diag-tool-sessions")
    rust_repo_path: str = ""          # location of the Rust node repo (for cargo build)
    # Empty by default -> the Test tab falls back to <this_repo>/tests/scenarios.
    scenarios_path: str = ""
    # Test mode override for the Test tab and Timing tab, both of which
    # otherwise DERIVE simulated-vs-hardware automatically from whether
    # any hardware node is configured+enabled. "auto" keeps that
    # derivation; "simulated"/"hardware" force one or the other
    # regardless of what's configured -- e.g. running a quick simulated
    # check without disabling all your hardware nodes first.
    test_mode: str = "auto"           # "auto" | "simulated" | "hardware"
    # Cross-compile
    target_triple: str = ""           # empty = host triple
    binary_name: str = "node"
    build_features: str = "diagnostic"  # required for the JSON diag channel
    # probation_cycles baked into the config of HARDWARE test deploys
    # (Test tab). Simulation keeps the NodeSpec default (10). On the Pis
    # the harness needs ~300 ms from seeing a readmit in the SSH-tailed
    # log to getting a diag command acked, while 10 cycles closed the
    # probation window after ~130 ms, so probation-timing scenarios
    # (T20, T21) never hit the window. Deliberate, visible deviation
    # from the simulated config, see test_t20's docstring.
    hw_probation_cycles: int = 100
    # Hardware nodes: JSON-encoded list of HardwareNode dicts.
    hardware_nodes_json: str = "[]"
    # MooN package building (Package tab) -- see tools/build-moon-package.sh
    # and stage6-moon/03-moon-package-service/files/moon-pkg-load.sh in the
    # pi-gen repo for the exact format this has to match.
    moon_pi_gen_repo_path: str = ""   # pi-gen/moon-pi-images checkout (for tools/build-moon-package.sh)
    moon_signing_key_path: str = ""   # ed25519 private key .pem used to sign packages -- keep this OFF the nodes themselves

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
