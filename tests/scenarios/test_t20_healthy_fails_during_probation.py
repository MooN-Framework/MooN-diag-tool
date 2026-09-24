"""
T20 — A healthy node fails while another is in probation => failsafe.

Setup:     3 stable nodes (0, 1, 2).
Sequence:
    1. Shut down the target node (2) -> peers see it as Lost.
    2. Restart the target node (2) -> it is re-admitted and runs
       through the probation window (health = Probation).
    3. While node 2 is still in probation: shut down one of the two
       "healthy" nodes (0).

Expected:
    With only one verified-healthy node (node 1) and one node in
    probation, quorum can no longer be safely reached. Fix B (no
    unilateral exclude) prevents node 1 from continuing alone: it
    must go to failsafe.

Timing:
    We need to hit node 0's failure exactly INSIDE the probation
    window. Probation duration = probation_cycles (default 10) *
    cycle_ms (default 20) = ~200 ms. So: wait_for_log on the readmit
    event (Lost -> Probation) and trigger the shutdown IMMEDIATELY
    afterwards -- the same approach T13 (rejoin) uses.

    On hardware this race is much tighter than in simulation: the
    readmit line travels file log -> `ssh tail -F` -> harness, and the
    shutdown travels back over the diag multicast, before the observer
    can even start detecting node 0 as lost. If all of that takes
    longer than the probation window, node 2 is already Alive when
    node 0 goes, nodes 1 + 2 form a legitimate 2oo3 quorum and NOT
    failing safe is correct. The test then did not exercise its
    scenario at all. `_probation_window_report()` checks exactly that
    from the observer's own log timestamps, so a failure says whether
    the framework misbehaved or the precondition was never met.

    Measured on the Pi cluster with the default 10 cycles: probation
    ended ~130 ms after the readmit, the harness needed ~300 ms to the
    shutdown ack. Hardware deploys therefore use a wider window, set
    as "Probation cycles" in the Test tab (AppSettings.hw_probation_cycles).
"""
import re
import time
from datetime import datetime

from harness.assertions import (
    any_node_reached_failsafe,
    wait_node_died,
    wait_peer_health,
)


PROBATION_TARGET = 2   # node that should enter probation
FAIL_TARGET = 0        # healthy node that fails WHILE in probation
OBSERVER = 1            # remaining node -- must reach failsafe

_TS = re.compile(r"^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d+)Z")


def _ts(line: str):
    """UTC timestamp of a tracing log line, None if it has none."""
    m = _TS.match(line)
    if not m:
        return None
    try:
        return datetime.fromisoformat(m.group(1))
    except ValueError:
        return None


def _probation_window_report(lines: list[str]) -> str | None:
    """If the observer promoted the probation target to Alive after its
    readmit, describe it (with the window measured from the observer's
    own timestamps). None if no promotion was logged, i.e. node 2 was
    still in probation when the fabric reacted to node 0's loss."""
    readmit_idx = None
    pat_readmit = re.compile(rf"peer readmitted.*peer_id={PROBATION_TARGET}")
    for i, ln in enumerate(lines):
        if pat_readmit.search(ln):
            readmit_idx = i
    if readmit_idx is None:
        return None
    for ln in lines[readmit_idx + 1:]:
        if "peers promoted from Probation to Alive" in ln:
            t0, t1 = _ts(lines[readmit_idx]), _ts(ln)
            window = (
                f"{(t1 - t0).total_seconds() * 1000:.0f} ms after readmit"
                if t0 and t1 else "timestamp not parseable"
            )
            return f"node {PROBATION_TARGET} was promoted to Alive {window}"
    return None


def test_healthy_node_fails_during_probation(fabric_3):
    # 1. Shut down the probation target cleanly.
    assert fabric_3.diag.shutdown(PROBATION_TARGET) is not None, (
        "shutdown injection not acknowledged"
    )
    assert wait_node_died(fabric_3, PROBATION_TARGET, timeout=5.0)

    # 2. Peers see it as Lost.
    peer = wait_peer_health(fabric_3, OBSERVER, PROBATION_TARGET,
                            "Lost", timeout=8.0)
    assert peer is not None, (
        f"observer {OBSERVER} does not see target {PROBATION_TARGET} as Lost"
    )

    # 3. Restart the process -> readmit -> probation.
    fabric_3.restart_node(PROBATION_TARGET)

    # 4. Wait for the readmit log event on the observer. As soon as
    #    that happens, the node is in probation. Log-based instead of
    #    GetStatus because the probation window is too short to poll
    #    reliably (see T13).
    observer_node = fabric_3.nodes[OBSERVER]
    readmit_seen = observer_node.wait_for_log(
        rf"peer readmitted.*peer_id={PROBATION_TARGET}", timeout=15.0
    )
    t_seen = time.monotonic()
    assert readmit_seen, (
        f"observer {OBSERVER} did not readmit target {PROBATION_TARGET} "
        f"(Lost->Probation)"
    )

    # 5. IMMEDIATELY shut down the healthy node. We are now inside
    #    the probation window (~200 ms). _inject returns once the node
    #    has acknowledged (staged) the command.
    fabric_3.diag.shutdown(FAIL_TARGET)
    shutdown_ms = (time.monotonic() - t_seen) * 1000
    print(f"\n[T20] readmit seen -> shutdown acked: {shutdown_ms:.0f} ms", flush=True)

    # 6. The failure must actually happen.
    assert wait_node_died(fabric_3, FAIL_TARGET, timeout=5.0), (
        f"node {FAIL_TARGET} process not exited"
    )

    # 7. Expectation: some remaining node reaches failsafe. We
    #    deliberately don't check *which* one (the observer or the
    #    restarted probation target) -- Fix B only guarantees that no
    #    node keeps running alone.
    failed_id = any_node_reached_failsafe(fabric_3, timeout=15.0)
    if failed_id is None:
        promoted = _probation_window_report(observer_node.log_lines)
        assert promoted is None, (
            f"PRECONDITION NOT MET, not a framework finding: {promoted}, "
            f"before node {FAIL_TARGET}'s loss took effect (harness needed "
            f"{shutdown_ms:.0f} ms from seeing the readmit to the shutdown "
            f"ack alone). Nodes {OBSERVER} + {PROBATION_TARGET} were a "
            f"valid 2oo3 quorum, so not failing safe was correct. Widen "
            f"the probation window (Test tab: \"Probation cycles\")."
        )
    assert failed_id is not None, (
        "no node reached failsafe -- the fabric should not have kept "
        "running with 1 healthy node + 1 probation node"
    )
