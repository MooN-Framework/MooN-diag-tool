"""
T22 — Langzeit-Stabilitaetstest.

Setup:     3 Nodes stabil.
Sequenz:
    1. Eigene Fabric mit CYCLE_MS aufsetzen (nicht die 20ms Standard-
       Fabric — das war fuer 30s-Runs auf ausgelasteten CI-Maschinen
       zu ambitioniert und triggerte Phase-Timeouts + GoFailsafe-Cascade).
    2. Warten bis alle Nodes steady-state erreicht haben.
    3. Fuer DURATION_S Sekunden alle INSPECT_INTERVAL_S einen Snapshot
       (get_status + is_running) von jedem Node sammeln — ohne
       assertions dazwischen.
    4. Am Ende Report drucken (auch bei Erfolg) und Invarianten pruefen.

Invarianten:
    - Kein Node stirbt.
    - Kein Node landet dauerhaft in Failsafe/Isolation.
    - Zyklen laufen mit MIN_CYCLE_RATE der nominalen Rate (defensiver
      Threshold).
    - Alle Nodes machen ungefaehr gleich viele Zyklen (spread <=
      MAX_CYCLE_SPREAD).

Bei Node-Tod werden die letzten Log-Zeilen jedes Nodes in den Report
aufgenommen — damit sofort erkennbar ist WELCHER Node zuerst gestorben
ist und mit welcher `FailsafeReason` (bzw. Panic oder ohne Grund).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from harness.assertions import wait_node_state
from harness.fabric import Fabric, FabricOptions
from harness.config_gen import scaled_timing


CYCLE_MS = 50          # generoeser als Standard-Fabric — Langzeitstabilitaet != Timing-Grenztest
NOMINAL = 3
MINIMUM = 2
DURATION_S = 30.0
INSPECT_INTERVAL_S = 3.0
MIN_CYCLE_RATE = 0.60
MAX_CYCLE_SPREAD = 0.10
MAX_TRANSIENT_MISSES_PER_NODE = 1
LOG_TAIL_LINES = 30    # so viele Log-Zeilen pro Node bei Todesfall zeigen
SETUP_TIMEOUT_S = 15.0


@dataclass
class Snapshot:
    t: float
    node_id: int
    status: dict | None
    process_alive: bool


@dataclass
class NodeReport:
    node_id: int
    total_cycles: int = 0
    intervals: list[tuple[float, int]] = field(default_factory=list)
    diag_misses: int = 0
    process_died_at: float | None = None
    stuck_states: list[tuple[float, str]] = field(default_factory=list)
    peer_flaps: list[tuple[float, int, str]] = field(default_factory=list)


def _read_log_tail(node, n_lines: int) -> list[str]:
    """
    Beste-Absicht: liest die letzten n_lines aus dem Node-Log. Falls
    das Log-Attribut nicht vorhanden ist (harness-Version ohne
    log_path), leere Liste.
    """
    for attr in ("log_path", "log_file", "stdout_path"):
        p = getattr(node, attr, None)
        if p is None:
            continue
        try:
            path = Path(p)
            if not path.exists():
                continue
            with path.open("r", errors="replace") as f:
                lines = f.readlines()
            return [ln.rstrip("\n") for ln in lines[-n_lines:]]
        except OSError:
            continue
    return []


@pytest.fixture
def long_run_fabric(request, binary, work_dir):
    """
    Eigene Fabric-Instanz mit groesserer cycle_duration_ms. Ersetzt
    fabric_3 fuer diesen Test, damit die 20ms-Standardkonfig nicht
    limitiert.
    """
    if request.config.getoption("--fabric") == "hardware":
        pytest.skip("long-run stability uses simulated fabric only")
    opts = FabricOptions(
        nominal=NOMINAL,
        minimum=MINIMUM,
        binary=binary,
        work_dir=work_dir,
        cycle_duration_ms=CYCLE_MS,
        timing_overrides=scaled_timing(CYCLE_MS),
    )
    fab = Fabric(opts=opts)
    fab.start_all()
    try:
        if not fab.wait_operational(timeout=SETUP_TIMEOUT_S):
            fab.stop_all()
            pytest.fail(
                f"fabric did not reach operational within {SETUP_TIMEOUT_S}s"
            )
        yield fab
    finally:
        fab.stop_all()


@pytest.mark.slow
def test_long_run_stability(long_run_fabric):
    fabric = long_run_fabric
    node_ids = sorted(fabric.nodes.keys())
    expected_per_interval = int(INSPECT_INTERVAL_S * 1000 / CYCLE_MS)
    min_per_interval = max(1, int(expected_per_interval * MIN_CYCLE_RATE))

    # 1. Steady-state.
    for nid in node_ids:
        for name in ("ReadInputs", "ShareInputs", "ShareResult",
                     "PublishResult", "SendAck", "CycleSync"):
            if wait_node_state(fabric, nid, name, timeout=1.0) is not None:
                break
        else:
            pytest.fail(
                f"node {nid} did not reach steady-state within setup"
            )

    baseline: dict[int, int] = {}
    for nid in node_ids:
        status = fabric.diag.get_status(nid, timeout=2.0)
        assert status is not None, f"node {nid} does not respond at start"
        baseline[nid] = status.get("current_seq", 0)

    # 2. Snapshots sammeln.
    snapshots: list[Snapshot] = []
    start = time.monotonic()
    deadline = start + DURATION_S
    first_death_at: float | None = None

    while time.monotonic() < deadline:
        time.sleep(INSPECT_INTERVAL_S)
        t = time.monotonic() - start
        for nid in node_ids:
            alive = fabric.nodes[nid].is_running()
            if not alive and first_death_at is None:
                first_death_at = t
            try:
                status = fabric.diag.get_status(nid, timeout=0.5)
            except Exception:
                status = None
            snapshots.append(Snapshot(t=t, node_id=nid, status=status,
                                      process_alive=alive))

    # 3. Analyse.
    reports = {nid: NodeReport(node_id=nid) for nid in node_ids}
    last_seq: dict[int, int] = dict(baseline)
    last_t: dict[int, float] = {nid: 0.0 for nid in node_ids}

    for snap in snapshots:
        rep = reports[snap.node_id]
        if not snap.process_alive and rep.process_died_at is None:
            rep.process_died_at = snap.t
        if snap.status is None:
            rep.diag_misses += 1
            continue
        state = snap.status.get("node_state", "")
        if state in ("Failsafe", "Isolation"):
            rep.stuck_states.append((snap.t, state))
        cur_seq = snap.status.get("current_seq", 0)
        delta = cur_seq - last_seq[snap.node_id]
        rep.intervals.append((snap.t, delta))
        last_seq[snap.node_id] = cur_seq
        last_t[snap.node_id] = snap.t
        for peer in snap.status.get("peers", []):
            if peer.get("health") != "Alive":
                rep.peer_flaps.append(
                    (snap.t, peer.get("id", -1), peer.get("health", "?"))
                )

    for nid in node_ids:
        reports[nid].total_cycles = last_seq[nid] - baseline[nid]

    # 4. Report — immer drucken.
    lines = [
        f"long-run stability report",
        f"  config:     nominal={NOMINAL}, minimum={MINIMUM}, "
        f"cycle_duration_ms={CYCLE_MS}",
        f"  duration:   {DURATION_S:.1f}s, interval={INSPECT_INTERVAL_S:.1f}s",
        f"  thresholds: expected={expected_per_interval} cycles/interval, "
        f"min={min_per_interval} (rate={MIN_CYCLE_RATE:.0%}), "
        f"spread<={MAX_CYCLE_SPREAD:.0%}",
    ]
    if first_death_at is not None:
        lines.append(f"  ! FIRST DEATH DETECTED at t={first_death_at:.2f}s")
    for nid in node_ids:
        r = reports[nid]
        rate_hz = r.total_cycles / DURATION_S if DURATION_S else 0.0
        lines.append(
            f"  node {nid}: total={r.total_cycles} cycles ({rate_hz:.1f} Hz), "
            f"diag_misses={r.diag_misses}, "
            f"failsafe_snapshots={len(r.stuck_states)}, "
            f"peer_flaps={len(r.peer_flaps)}, "
            f"died_at={'-' if r.process_died_at is None else f'{r.process_died_at:.1f}s'}"
        )
        for t, s in r.stuck_states[:3]:
            lines.append(f"    stuck at {t:.1f}s: {s}")
        for t, pid, h in r.peer_flaps[:3]:
            lines.append(f"    peer flap at {t:.1f}s: peer {pid}={h}")

    # 5. Log-Tails bei Todesfaellen (auch fuer live-Nodes zur Kontext-Sicht).
    if first_death_at is not None:
        lines.append("")
        lines.append("=" * 72)
        lines.append(
            f"log tails (last {LOG_TAIL_LINES} lines per node) — "
            f"look for Failsafe/panic/error"
        )
        for nid in node_ids:
            tail = _read_log_tail(fabric.nodes[nid], LOG_TAIL_LINES)
            lines.append(f"--- node {nid} log ---")
            if not tail:
                lines.append("  (no log file available on this harness)")
            else:
                lines.extend(f"  {ln}" for ln in tail)
        lines.append("=" * 72)

    print("\n" + "\n".join(lines))

    # 6. Assertions gesammelt.
    failures: list[str] = []
    for nid in node_ids:
        r = reports[nid]
        if r.process_died_at is not None:
            failures.append(f"node {nid} process died at {r.process_died_at:.1f}s")
        if r.diag_misses > MAX_TRANSIENT_MISSES_PER_NODE:
            failures.append(
                f"node {nid} had {r.diag_misses} diag timeouts "
                f"(max {MAX_TRANSIENT_MISSES_PER_NODE})"
            )
        if len(r.stuck_states) > MAX_TRANSIENT_MISSES_PER_NODE:
            failures.append(
                f"node {nid} was in Failsafe/Isolation for "
                f"{len(r.stuck_states)} snapshots"
            )
        if len(r.peer_flaps) > MAX_TRANSIENT_MISSES_PER_NODE:
            failures.append(
                f"node {nid} saw {len(r.peer_flaps)} non-Alive peer snapshots"
            )
        bad_intervals = [
            (t, d) for (t, d) in r.intervals if d < min_per_interval
        ]
        if bad_intervals:
            worst = min(bad_intervals, key=lambda td: td[1])
            failures.append(
                f"node {nid} had {len(bad_intervals)} slow intervals "
                f"(worst: {worst[1]} cycles at t={worst[0]:.1f}s, "
                f"min={min_per_interval})"
            )

    counts = [reports[nid].total_cycles for nid in node_ids]
    if counts and max(counts) > 0:
        spread = (max(counts) - min(counts)) / max(counts)
        if spread > MAX_CYCLE_SPREAD:
            failures.append(
                f"cycle count spread too large: min={min(counts)} "
                f"max={max(counts)} spread={spread:.2%} "
                f"(max {MAX_CYCLE_SPREAD:.0%})"
            )

    assert not failures, "\n".join(["stability check failed:"] + failures)