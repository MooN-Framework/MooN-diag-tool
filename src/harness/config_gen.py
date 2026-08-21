"""
Generate + verify node TOML configs.

Every rendered config carries an `[integrity]` section with a
SHA-256 checksum over the rest of the file, so the Rust node can
detect corrupted or tampered configs at load time.

Canonical form (identical on Python and Rust side, both languages
must produce the same bytes for the same config):

    <dotted.path>=<json-encoded-value>\\n
    ...

- keys are the full dotted TOML path (e.g. `transport.multicast_group`)
- lines sorted by key (byte-wise, so alphabetically)
- values encoded as JSON (`"str"`, `42`, `true`) — this sidesteps every
  ambiguity in TOML value serialization
- LF newlines; no trailing newline
- the top-level `integrity` key is excluded when computing the digest

The Rust side implements the same walk with `toml::Value` + `sha2`.
See docs/rust_fix_config_checksum.md for the patch.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any


CHECKSUM_ALGO = "sha256"


@dataclass
class NodeSpec:
    own_id: int
    nominal: int
    minimum: int
    fabric_port: int = 5555
    diag_port: int = 6666
    fabric_group: str = "239.10.0.1"
    diag_group: str = "239.10.0.2"
    interface: str = "lo"
    # Timing — sensible defaults for fast local tests.
    cycle_duration_ms: int = 20
    share_inputs_offset_ms: int = 5
    share_result_offset_ms: int = 10
    send_ack_offset_ms: int = 14
    crc_offset_ms: int = 17
    init_sync_timeout_ms: int = 2000
    peer_sync_timeout_ms: int = 500
    cycle_sync_timeout_ms: int = 10
    error_mgmt_timeout_ms: int = 20
    state_sync_timeout_ms: int = 500
    resync_returning_timeout_ms: int = 5000
    resync_healthy_timeout_ms: int = 500
    send_interval_ms: int = 1
    stale_frame_threshold_ms: int = 100
    resync_interval_cycles: int = 500
    probation_cycles: int = 10


# ---- checksum machinery ------------------------------------------------

def _flatten(value: Any, prefix: str = "") -> list[tuple[str, Any]]:
    """Recursively flatten a nested dict to (dotted_path, leaf) pairs."""
    if isinstance(value, dict):
        out: list[tuple[str, Any]] = []
        for k in value.keys():
            sub_prefix = f"{prefix}.{k}" if prefix else k
            out.extend(_flatten(value[k], sub_prefix))
        return out
    return [(prefix, value)]


def canonical_bytes(cfg: dict[str, Any]) -> bytes:
    """
    Produce the deterministic byte stream over which the checksum is
    computed. Excludes the top-level `integrity` section.
    """
    filtered = {k: v for k, v in cfg.items() if k != "integrity"}
    leaves = _flatten(filtered)
    # sort by path AFTER flattening so nested keys are ordered globally,
    # not just within their parent — matches the Rust walk.
    leaves.sort(key=lambda kv: kv[0])
    lines = [f"{k}={json.dumps(v)}" for k, v in leaves]
    return "\n".join(lines).encode("utf-8")


def compute_checksum(cfg: dict[str, Any]) -> str:
    """SHA-256 hex digest over the canonical byte stream."""
    return hashlib.sha256(canonical_bytes(cfg)).hexdigest()


def spec_to_dict(spec: NodeSpec) -> dict[str, Any]:
    """The dict shape the Rust NodeConfig expects."""
    return {
        "own_id": spec.own_id,
        "participants": {
            "nominal": spec.nominal,
            "minimum": spec.minimum,
            "probation_cycles": spec.probation_cycles,
        },
        "timing": {
            "cycle_duration_ms": spec.cycle_duration_ms,
            "share_inputs_offset_ms": spec.share_inputs_offset_ms,
            "share_result_offset_ms": spec.share_result_offset_ms,
            "send_ack_offset_ms": spec.send_ack_offset_ms,
            "crc_offset_ms": spec.crc_offset_ms,
            "init_sync_timeout_ms": spec.init_sync_timeout_ms,
            "peer_sync_timeout_ms": spec.peer_sync_timeout_ms,
            "cycle_sync_timeout_ms": spec.cycle_sync_timeout_ms,
            "error_mgmt_timeout_ms": spec.error_mgmt_timeout_ms,
            "state_sync_timeout_ms": spec.state_sync_timeout_ms,
            "resync_returning_timeout_ms": spec.resync_returning_timeout_ms,
            "resync_healthy_timeout_ms": spec.resync_healthy_timeout_ms,
            "send_interval_ms": spec.send_interval_ms,
            "stale_frame_threshold_ms": spec.stale_frame_threshold_ms,
            "resync_interval_cycles": spec.resync_interval_cycles,
        },
        "transport": {
            "interface": spec.interface,
            "multicast_group": spec.fabric_group,
            "port": spec.fabric_port,
        },
        "diagnostic": {
            "enabled": True,
            "interface": spec.interface,
            "multicast_group": spec.diag_group,
            "port": spec.diag_port,
        },
    }


# ---- rendering ---------------------------------------------------------

def render_toml_str(spec: NodeSpec) -> str:
    """
    Serialize a NodeSpec to a TOML string, including the trailing
    [integrity] section with the computed checksum.
    """
    cfg = spec_to_dict(spec)
    digest = compute_checksum(cfg)

    return (
        f"own_id = {spec.own_id}\n"
        f"\n"
        f"[participants]\n"
        f"nominal          = {spec.nominal}\n"
        f"minimum          = {spec.minimum}\n"
        f"probation_cycles = {spec.probation_cycles}\n"
        f"\n"
        f"[timing]\n"
        f"cycle_duration_ms = {spec.cycle_duration_ms}\n"
        f"\n"
        f"share_inputs_offset_ms  = {spec.share_inputs_offset_ms}\n"
        f"share_result_offset_ms  = {spec.share_result_offset_ms}\n"
        f"send_ack_offset_ms      = {spec.send_ack_offset_ms}\n"
        f"crc_offset_ms           = {spec.crc_offset_ms}\n"
        f"\n"
        f"init_sync_timeout_ms        = {spec.init_sync_timeout_ms}\n"
        f"peer_sync_timeout_ms        = {spec.peer_sync_timeout_ms}\n"
        f"cycle_sync_timeout_ms       = {spec.cycle_sync_timeout_ms}\n"
        f"error_mgmt_timeout_ms       = {spec.error_mgmt_timeout_ms}\n"
        f"state_sync_timeout_ms       = {spec.state_sync_timeout_ms}\n"
        f"resync_returning_timeout_ms = {spec.resync_returning_timeout_ms}\n"
        f"resync_healthy_timeout_ms   = {spec.resync_healthy_timeout_ms}\n"
        f"\n"
        f"send_interval_ms         = {spec.send_interval_ms}\n"
        f"stale_frame_threshold_ms = {spec.stale_frame_threshold_ms}\n"
        f"resync_interval_cycles   = {spec.resync_interval_cycles}\n"
        f"\n"
        f'[transport]\n'
        f'interface       = "{spec.interface}"\n'
        f'multicast_group = "{spec.fabric_group}"\n'
        f"port            = {spec.fabric_port}\n"
        f"\n"
        f"[diagnostic]\n"
        f"enabled         = true\n"
        f'interface       = "{spec.interface}"\n'
        f'multicast_group = "{spec.diag_group}"\n'
        f"port            = {spec.diag_port}\n"
        f"\n"
        f"# Do not edit the [integrity] section by hand — regenerate with\n"
        f"# python -m harness.config_gen if you change anything above.\n"
        f"[integrity]\n"
        f'algo     = "{CHECKSUM_ALGO}"\n'
        f'checksum = "{digest}"\n'
    )


def render_config(spec: NodeSpec, out_path: Path) -> Path:
    """Write a full TOML for this node and return the path."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(render_toml_str(spec))
    return out_path


# ---- verification (used by tests and by the GUI Config tab) -----------

class ChecksumError(Exception):
    pass


def verify_toml_str(text: str) -> dict[str, Any]:
    """
    Parse a TOML text, verify its integrity section, return the parsed
    dict on success. Raises ChecksumError on any problem.
    """
    try:
        import tomllib  # stdlib in 3.11+
    except ImportError:  # pragma: no cover
        import tomli as tomllib   # type: ignore

    try:
        cfg = tomllib.loads(text)
    except Exception as e:  # noqa: BLE001 — surface parse errors uniformly
        raise ChecksumError(f"TOML parse failed: {e}") from e

    integrity = cfg.get("integrity")
    if not isinstance(integrity, dict):
        raise ChecksumError("missing [integrity] section")
    algo = integrity.get("algo")
    if algo != CHECKSUM_ALGO:
        raise ChecksumError(f"unsupported checksum algo: {algo!r}")
    expected = integrity.get("checksum")
    if not isinstance(expected, str):
        raise ChecksumError("[integrity].checksum missing or not a string")

    actual = compute_checksum(cfg)
    if actual != expected:
        raise ChecksumError(
            f"checksum mismatch: file says {expected}, computed {actual}"
        )
    return cfg


def verify_toml_file(path: Path) -> dict[str, Any]:
    return verify_toml_str(Path(path).read_text())


# ---- convenience -------------------------------------------------------

def scaled_timing(cycle_ms: int) -> dict[str, int]:
    """
    Scale in-cycle offsets proportional to the 20 ms defaults
    (cycle=20 -> SI=5, SR=10, SA=14, CRC=17). Rounds to whole ms.

    For very small cycle_ms the offsets would collapse; a floor of 1 ms
    keeps them strictly increasing so validate() still accepts the config.
    """
    ratio = cycle_ms / 20
    si = max(1, round(5 * ratio))
    sr = max(si + 1, round(10 * ratio))
    sa = max(sr + 1, round(14 * ratio))
    crc = max(sa + 1, round(17 * ratio))
    return dict(
        cycle_duration_ms=cycle_ms,
        share_inputs_offset_ms=si,
        share_result_offset_ms=sr,
        send_ack_offset_ms=sa,
        crc_offset_ms=crc,
    )


def make_spec(
    own_id: int, nominal: int, minimum: int,
    cycle_ms: int, *,
    fabric_group: str = "239.10.0.1", fabric_port: int = 5555,
    diag_group: str = "239.10.0.2", diag_port: int = 6666,
    interface: str = "lo",
    init_sync_timeout_ms: int = 2000,
) -> NodeSpec:
    """Build a NodeSpec and apply scaled_timing.

    init_sync_timeout_ms defaults to the same 2000ms NodeSpec used to
    default to, kept as an explicit parameter because it often needs to
    be much higher on real hardware than in simulated/loopback tests
    (nodes powering up at different times, slower boot, etc.) -- unlike
    the in-cycle offsets, it isn't derived from cycle_ms so it's passed
    straight through rather than going via scaled_timing().
    """
    overrides = scaled_timing(cycle_ms)
    base = NodeSpec(
        own_id=own_id, nominal=nominal, minimum=minimum,
        fabric_group=fabric_group, fabric_port=fabric_port,
        diag_group=diag_group, diag_port=diag_port,
        interface=interface,
        init_sync_timeout_ms=init_sync_timeout_ms,
    )
    return replace(base, **overrides)


# CLI: `python -m harness.config_gen verify path.toml` and
#      `python -m harness.config_gen show path.toml`.
if __name__ == "__main__":  # pragma: no cover
    import sys
    if len(sys.argv) < 3 or sys.argv[1] not in ("verify", "show"):
        print("usage: python -m harness.config_gen verify|show <path.toml>")
        sys.exit(2)
    p = Path(sys.argv[2])
    if sys.argv[1] == "verify":
        try:
            verify_toml_file(p)
            print(f"OK  {p}")
        except ChecksumError as e:
            print(f"FAIL {p}: {e}")
            sys.exit(1)
    else:
        cfg = verify_toml_file(p)
        print(json.dumps(cfg, indent=2, default=str))
