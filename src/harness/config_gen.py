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

The Rust side implements the same walk with `toml::Value` + `sha2`
(see `NodeConfig::verify_and_parse` in the voting-node repo).
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
    init_sync_timeout_ms: int = 15_000
    # The remaining timeouts are what derive_timing(20) produces; keep
    # them in sync with it so a NodeSpec built by hand at the default
    # cycle matches a derived one.
    clock_sync_timeout_ms: int = 100
    cycle_sync_timeout_ms: int = 10
    error_mgmt_timeout_ms: int = 20
    state_sync_timeout_ms: int = 60
    resync_returning_timeout_ms: int = 500
    resync_healthy_timeout_ms: int = 100
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
            "clock_sync_timeout_ms": spec.clock_sync_timeout_ms,
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
    budget = timing_budget(cfg["timing"])
    # Comments are not part of the canonical byte stream (the checksum is
    # computed over parsed values), so this block is free to change
    # without invalidating the digest.
    notes = (
        f"# Timing derived from cycle_duration_ms = {spec.cycle_duration_ms}.\n"
        f"# In-cycle offsets are fractions of the cycle; the timeouts below\n"
        f"# take the larger of a cycle-proportional term and a round-trip\n"
        f"# floor in send intervals. Regenerate rather than editing by hand.\n"
        f"#\n"
        f"#   in-cycle used / slack   {budget['in_cycle_used_ms']}ms / "
        f"{budget['in_cycle_slack_ms']}ms\n"
        f"#   cycle if CycleSync hits its timeout   "
        f"{budget['worst_case_cycle_ms']}ms\n"
        f"#   anchor to failsafe, per trigger:\n"
        f"#     share_inputs {budget['failsafe_via_share_inputs_ms']}ms   "
        f"share_result {budget['failsafe_via_share_result_ms']}ms   "
        f"send_ack {budget['failsafe_via_send_ack_ms']}ms\n"
        f"#     crc {budget['failsafe_via_crc_ms']}ms   "
        f"cycle_sync {budget['failsafe_via_cycle_sync_ms']}ms   "
        f"clock_sync {budget['failsafe_via_clock_sync_ms']}ms   "
        f"state_sync {budget['failsafe_via_state_sync_ms']}ms\n"
    )

    return (
        f"own_id = {spec.own_id}\n"
        f"\n"
        f"[participants]\n"
        f"nominal          = {spec.nominal}\n"
        f"minimum          = {spec.minimum}\n"
        f"probation_cycles = {spec.probation_cycles}\n"
        f"\n"
        f"{notes}"
        f"[timing]\n"
        f"cycle_duration_ms = {spec.cycle_duration_ms}\n"
        f"\n"
        f"share_inputs_offset_ms  = {spec.share_inputs_offset_ms}\n"
        f"share_result_offset_ms  = {spec.share_result_offset_ms}\n"
        f"send_ack_offset_ms      = {spec.send_ack_offset_ms}\n"
        f"crc_offset_ms           = {spec.crc_offset_ms}\n"
        f"\n"
        f"init_sync_timeout_ms        = {spec.init_sync_timeout_ms}\n"
        f"clock_sync_timeout_ms       = {spec.clock_sync_timeout_ms}\n"
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

# ---- timing derivation -------------------------------------------------
#
# Everything below turns a single knob, the cycle duration, into a
# complete and internally consistent timing section. Two different
# units are at work and mixing them up is what produced the original
# hand-written numbers, where clock_sync sat at 500 ms next to a 20 ms
# cycle:
#
#   * Phase deadlines inside the cycle are fractions of the cycle. They
#     scale with it, because they partition it.
#   * Phases that wait for a peer to answer are bounded by round trips,
#     not by the cycle. Their floor is a multiple of send_interval and
#     does NOT shrink when the cycle does. Scaling them with the cycle
#     alone breaks them at small cycle durations; pinning them to a
#     constant makes them absurd at large ones. Each therefore gets
#     both a cycle-proportional term and a round-trip floor, and takes
#     whichever is larger.

# Protocol facts the derivation depends on. Keep in sync with the Rust
# side; a mismatch shows up as a phase that cannot finish in time.
SAMPLES_PER_PEER = 8          # clock_sync.rs — Cristian rounds per peer
CYCLE_SYNC_ROUNDS = 4         # beacons needed to see every peer once
ERROR_MGMT_ROUNDS = 6         # one exclusion-vote round trip plus margin
STATE_SYNC_ROUNDS = 24        # snapshot in, apply, ack out, with margin
RESYNC_ROUNDS = 40            # readmit round trip, generous

# In-cycle phase deadlines as a fraction of the cycle. The 20 ms
# reference config maps to 5 / 10 / 14 / 17 ms.
_PHASE_FRACTIONS = {
    "share_inputs_offset_ms": 5 / 20,
    "share_result_offset_ms": 10 / 20,
    "send_ack_offset_ms": 14 / 20,
    "crc_offset_ms": 17 / 20,
}

# Timeouts of the phases outside the cycle, as (cycles, send_intervals).
# The generated value is the larger of the two. Buffers are deliberately
# roomy: overshooting costs detection latency in a fault case, but
# undershooting fails a healthy fabric, and three of these five route
# straight to Failsafe.
_TIMEOUT_TERMS = {
    # Barrier at the cycle boundary, sees one beacon per peer.
    "cycle_sync_timeout_ms": (0.5, CYCLE_SYNC_ROUNDS),
    # One exclusion-vote exchange. A full cycle is plenty.
    "error_mgmt_timeout_ms": (1.0, ERROR_MGMT_ROUNDS),
    # Eight Cristian rounds per peer, one per send interval, times five
    # for loss and jitter. Note that a peer stops answering TimeSyncReq
    # once its own samples are complete, so a node that falls behind has
    # no second chance — hence the wide margin.
    "clock_sync_timeout_ms": (5.0, SAMPLES_PER_PEER * 5),
    # Snapshot exchange plus the adopting node's ack.
    "state_sync_timeout_ms": (3.0, STATE_SYNC_ROUNDS),
    # Healthy side of a rejoin: completes after one round trip, but has
    # to wait for the returning node to show up.
    "resync_healthy_timeout_ms": (5.0, RESYNC_ROUNDS),
    # Returning side: has to survive the peers noticing the rejoin,
    # aggregating votes over a full cycle and entering their own resync
    # phase. Must stay well above the healthy side, otherwise the
    # healthy nodes give up and resume cycling while the returning node
    # still waits for frames nobody sends any more.
    "resync_returning_timeout_ms": (25.0, RESYNC_ROUNDS * 5),
    # How old a received frame may be before it is discarded.
    "stale_frame_threshold_ms": (5.0, RESYNC_ROUNDS),
}


def derive_timing(cycle_ms: int, send_interval_ms: int = 1) -> dict[str, int]:
    """
    Derive the complete timing section from the cycle duration.

    Returns every field of the `[timing]` table except
    `init_sync_timeout_ms` and `resync_interval_cycles`: the first is a
    boot-time property of the deployment rather than of the cycle, the
    second is already expressed in cycles.

    Raises ValueError when the requested cycle is too short to hold four
    strictly increasing whole-millisecond phase offsets. That check used
    to surface only after a deploy, as a panic in the node's own
    `CycleTiming::validate`.
    """
    if cycle_ms < 1:
        raise ValueError(f"cycle_ms must be >= 1, got {cycle_ms}")
    if send_interval_ms < 1:
        raise ValueError(f"send_interval_ms must be >= 1, got {send_interval_ms}")

    si = max(1, round(_PHASE_FRACTIONS["share_inputs_offset_ms"] * cycle_ms))
    sr = max(si + 1, round(_PHASE_FRACTIONS["share_result_offset_ms"] * cycle_ms))
    sa = max(sr + 1, round(_PHASE_FRACTIONS["send_ack_offset_ms"] * cycle_ms))
    crc = max(sa + 1, round(_PHASE_FRACTIONS["crc_offset_ms"] * cycle_ms))
    if crc >= cycle_ms:
        raise ValueError(
            f"cycle_ms={cycle_ms} is too small for this scaling scheme: "
            f"the four in-cycle phase offsets need strictly-increasing "
            f"whole-ms spacing (share_inputs={si}, share_result={sr}, "
            f"send_ack={sa}, crc={crc}), which no longer fits inside a "
            f"{cycle_ms}ms cycle (crc_offset_ms must be < cycle_duration_ms). "
            f"Minimum viable cycle_ms here is 6."
        )

    timing = dict(
        cycle_duration_ms=cycle_ms,
        share_inputs_offset_ms=si,
        share_result_offset_ms=sr,
        send_ack_offset_ms=sa,
        crc_offset_ms=crc,
        send_interval_ms=send_interval_ms,
    )
    for name, (cycles, rounds) in _TIMEOUT_TERMS.items():
        timing[name] = max(round(cycles * cycle_ms), rounds * send_interval_ms)

    validate_timing(timing)
    return timing


def scaled_timing(cycle_ms: int) -> dict[str, int]:
    """
    Backwards-compatible alias for `derive_timing`.

    It used to return the four in-cycle offsets only, which left callers
    combining a scaled cycle with unscaled timeouts. It now returns the
    full timing set, so passing it as `timing_overrides` yields a
    consistent config.
    """
    return derive_timing(cycle_ms)


def validate_timing(timing: dict[str, int]) -> None:
    """
    Check a timing section for internal consistency.

    Covers the rules the node enforces in `CycleTiming::validate` plus
    the ones it does not, so a bad combination is caught here instead of
    on the target. Raises ValueError with a specific message.

    Only combinations that cannot work at all are errors. Combinations
    that are merely tight are reported by `timing_warnings` instead, so
    the timing sweep can still probe them.
    """
    c = timing["cycle_duration_ms"]
    s = timing["send_interval_ms"]
    offsets = [
        ("share_inputs_offset_ms", timing["share_inputs_offset_ms"]),
        ("share_result_offset_ms", timing["share_result_offset_ms"]),
        ("send_ack_offset_ms", timing["send_ack_offset_ms"]),
        ("crc_offset_ms", timing["crc_offset_ms"]),
    ]

    for name, value in offsets:
        if value <= 0:
            raise ValueError(f"{name} must be > 0, got {value}")
    for (a_name, a), (b_name, b) in zip(offsets, offsets[1:]):
        if b <= a:
            raise ValueError(
                f"{b_name}={b} must be strictly greater than {a_name}={a}"
            )
    if offsets[-1][1] >= c:
        raise ValueError(
            f"crc_offset_ms={offsets[-1][1]} must be < cycle_duration_ms={c}"
        )

    # Every in-cycle phase needs room for at least one send attempt. The
    # node only checks the first offset against send_interval, so a
    # config where share_result sits inside share_inputs' send interval
    # passes there and then times out on the target every cycle.
    for name, budget in _phase_budgets(timing):
        if budget < s:
            raise ValueError(
                f"{name} leaves only {budget}ms of phase budget, which is "
                f"below one send interval ({s}ms) — the phase cannot send "
                f"anything before its deadline"
            )

    need = (SAMPLES_PER_PEER + 1) * s
    if timing["clock_sync_timeout_ms"] < need:
        raise ValueError(
            f"clock_sync_timeout_ms={timing['clock_sync_timeout_ms']} is below "
            f"the {SAMPLES_PER_PEER} Cristian rounds the phase needs at "
            f"send_interval_ms={s} (>= {need}ms)"
        )
    if timing["resync_healthy_timeout_ms"] >= timing["resync_returning_timeout_ms"]:
        raise ValueError(
            f"resync_healthy_timeout_ms={timing['resync_healthy_timeout_ms']} "
            f"must stay below resync_returning_timeout_ms="
            f"{timing['resync_returning_timeout_ms']}, otherwise the healthy "
            f"nodes resume cycling while the returning node still waits"
        )
    if timing["stale_frame_threshold_ms"] < 2 * c:
        raise ValueError(
            f"stale_frame_threshold_ms={timing['stale_frame_threshold_ms']} is "
            f"below two cycles ({2 * c}ms); normal jitter would start "
            f"discarding healthy frames"
        )
    if timing["error_mgmt_timeout_ms"] < 3 * s:
        raise ValueError(
            f"error_mgmt_timeout_ms={timing['error_mgmt_timeout_ms']} leaves no "
            f"room for an exclusion-vote round trip at send_interval_ms={s}"
        )


def _phase_budgets(timing: dict[str, int]) -> list[tuple[str, int]]:
    """Net time each in-cycle phase has between its predecessor and its
    own deadline."""
    offsets = [
        ("share_inputs_offset_ms", timing["share_inputs_offset_ms"]),
        ("share_result_offset_ms", timing["share_result_offset_ms"]),
        ("send_ack_offset_ms", timing["send_ack_offset_ms"]),
        ("crc_offset_ms", timing["crc_offset_ms"]),
    ]
    out = [(offsets[0][0], offsets[0][1])]
    for (_, a), (b_name, b) in zip(offsets, offsets[1:]):
        out.append((b_name, b - a))
    return out


def timing_warnings(timing: dict[str, int]) -> list[str]:
    """
    Combinations that are legal but tight. Advisory only — the timing
    sweep deliberately probes configs that trip these.
    """
    out: list[str] = []
    s = timing["send_interval_ms"]
    c = timing["cycle_duration_ms"]

    for name, budget in _phase_budgets(timing):
        if budget < 2 * s:
            out.append(
                f"{name}: {budget}ms budget allows only one send attempt at "
                f"send_interval_ms={s}; a single lost frame times the phase out"
            )

    pathological = timing["crc_offset_ms"] + timing["cycle_sync_timeout_ms"]
    if pathological > c:
        out.append(
            f"in-cycle offsets ({timing['crc_offset_ms']}ms) plus a CycleSync "
            f"that runs into its timeout ({timing['cycle_sync_timeout_ms']}ms) "
            f"exceed the {c}ms cycle by {pathological - c}ms; expect a cycle "
            f"overrun whenever the barrier does not complete promptly"
        )
    return out


def timing_budget(timing: dict[str, int]) -> dict[str, int]:
    """
    Report how the cycle budget is spent and how long each timeout path
    takes to reach Failsafe. Pure reporting, no validation.

    `worst_case_cycle_ms` is the cycle length when CycleSync runs into
    its timeout. It exceeds the cycle duration by design: CycleSync is a
    barrier at the cycle boundary, so its timeout bounds a pathological
    case and is not part of the nominal budget. On the nominal path the
    barrier completes within one or two send intervals.
    """
    c = timing["cycle_duration_ms"]
    crc = timing["crc_offset_ms"]
    em = timing["error_mgmt_timeout_ms"]
    return {
        "in_cycle_used_ms": crc,
        "in_cycle_slack_ms": c - crc,
        "worst_case_cycle_ms": crc + timing["cycle_sync_timeout_ms"],
        # Time from the cycle anchor to Failsafe, per trigger.
        "failsafe_via_share_inputs_ms": timing["share_inputs_offset_ms"] + em,
        "failsafe_via_share_result_ms": timing["share_result_offset_ms"] + em,
        "failsafe_via_send_ack_ms": timing["send_ack_offset_ms"] + em,
        "failsafe_via_crc_ms": crc,
        "failsafe_via_cycle_sync_ms": timing["cycle_sync_timeout_ms"] + em,
        "failsafe_via_clock_sync_ms": timing["clock_sync_timeout_ms"],
        "failsafe_via_state_sync_ms": timing["state_sync_timeout_ms"],
    }


def make_spec(
    own_id: int, nominal: int, minimum: int,
    cycle_ms: int, *,
    fabric_group: str = "239.10.0.1", fabric_port: int = 5555,
    diag_group: str = "239.10.0.2", diag_port: int = 6666,
    interface: str = "lo",
    init_sync_timeout_ms: int = 15_000,
    send_interval_ms: int = 1,
) -> NodeSpec:
    """Build a NodeSpec with a timing section derived from cycle_ms.

    init_sync_timeout_ms defaults to the same 15000ms NodeSpec used to
    default to, kept as an explicit parameter because it often needs to
    be much higher on real hardware than in simulated/loopback tests
    (nodes powering up at different times, slower boot, etc.) -- unlike
    the in-cycle offsets, it isn't derived from cycle_ms so it's passed
    straight through rather than going via derive_timing().
    """
    overrides = derive_timing(cycle_ms, send_interval_ms)
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
