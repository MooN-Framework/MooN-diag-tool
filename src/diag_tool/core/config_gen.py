"""
TOML config generator for a single node. Format matches
harness/config_gen.py so the same binary works in both simulated and
hardware sweeps.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path


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
    # Timing
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


def scaled_timing(cycle_ms: int) -> dict[str, int]:
    """Scale the in-cycle offsets proportional to the 20 ms defaults."""
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


def render_toml(spec: NodeSpec) -> str:
    return f"""own_id = {spec.own_id}

[participants]
nominal          = {spec.nominal}
minimum          = {spec.minimum}
probation_cycles = {spec.probation_cycles}

[timing]
cycle_duration_ms = {spec.cycle_duration_ms}

share_inputs_offset_ms  = {spec.share_inputs_offset_ms}
share_result_offset_ms  = {spec.share_result_offset_ms}
send_ack_offset_ms      = {spec.send_ack_offset_ms}
crc_offset_ms           = {spec.crc_offset_ms}

init_sync_timeout_ms        = {spec.init_sync_timeout_ms}
peer_sync_timeout_ms        = {spec.peer_sync_timeout_ms}
cycle_sync_timeout_ms       = {spec.cycle_sync_timeout_ms}
error_mgmt_timeout_ms       = {spec.error_mgmt_timeout_ms}
state_sync_timeout_ms       = {spec.state_sync_timeout_ms}
resync_returning_timeout_ms = {spec.resync_returning_timeout_ms}
resync_healthy_timeout_ms   = {spec.resync_healthy_timeout_ms}

send_interval_ms         = {spec.send_interval_ms}
stale_frame_threshold_ms = {spec.stale_frame_threshold_ms}
resync_interval_cycles   = {spec.resync_interval_cycles}

[transport]
interface       = "{spec.interface}"
multicast_group = "{spec.fabric_group}"
port            = {spec.fabric_port}

[diagnostic]
enabled         = true
multicast_group = "{spec.diag_group}"
port            = {spec.diag_port}
"""


def render_to_file(spec: NodeSpec, out_path: Path) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(render_toml(spec))
    return out_path


def make_spec(
    own_id: int, nominal: int, minimum: int,
    cycle_ms: int, *,
    fabric_group: str, fabric_port: int,
    diag_group: str, diag_port: int,
    interface: str = "lo",
) -> NodeSpec:
    """Build a NodeSpec with proportional timing overrides applied."""
    overrides = scaled_timing(cycle_ms)
    base = NodeSpec(
        own_id=own_id,
        nominal=nominal,
        minimum=minimum,
        fabric_group=fabric_group,
        fabric_port=fabric_port,
        diag_group=diag_group,
        diag_port=diag_port,
        interface=interface,
    )
    return replace(base, **overrides)
