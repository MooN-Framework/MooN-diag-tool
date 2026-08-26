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
"""
from harness.assertions import (
    any_node_reached_failsafe,
    wait_node_died,
    wait_peer_health,
)


PROBATION_TARGET = 2   # node that should enter probation
FAIL_TARGET = 0        # healthy node that fails WHILE in probation
OBSERVER = 1            # remaining node -- must reach failsafe


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
    assert observer_node.wait_for_log(
        rf"peer readmitted.*peer_id={PROBATION_TARGET}", timeout=15.0
    ), (
        f"observer {OBSERVER} did not readmit target {PROBATION_TARGET} "
        f"(Lost->Probation)"
    )

    # 5. IMMEDIATELY shut down the healthy node. We are now inside
    #    the probation window (~200 ms). The shutdown command itself
    #    doesn't wait for confirmation -- it fires and returns.
    fabric_3.diag.shutdown(FAIL_TARGET)

    # 6. The failure must actually happen.
    assert wait_node_died(fabric_3, FAIL_TARGET, timeout=5.0), (
        f"node {FAIL_TARGET} process not exited"
    )

    # 7. Expectation: some remaining node reaches failsafe. We
    #    deliberately don't check *which* one (the observer or the
    #    restarted probation target) -- Fix B only guarantees that no
    #    node keeps running alone.
    failed_id = any_node_reached_failsafe(fabric_3, timeout=15.0)
    assert failed_id is not None, (
        "no node reached failsafe -- the fabric should not have kept "
        "running with 1 healthy node + 1 probation node"
    )
