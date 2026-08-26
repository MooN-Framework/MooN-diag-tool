"""
T11 — Quorum limit in 2oo3.

Setup:     3 stable nodes.
Injection: shutdown node 1, wait until excluded (cycle continues with
           2 nodes), then shutdown node 2.
Expected:  After the first shutdown: node 1 is excluded, the fabric
           continues with nodes 0 and 2. After the second: the single
           remaining node can no longer decide unilaterally (Fix B:
           reporters=1 → no exclude) → EM times out → StateTimeout →
           failsafe.
"""
import time

from harness.assertions import (
    wait_node_died,
    wait_peer_health,
)


def test_quorum_limit_2oo3(fabric_3):
    # 1. First failure.
    assert fabric_3.diag.shutdown(1)
    assert wait_node_died(fabric_3, 1, timeout=5.0)
    for observer in (0, 2):
        peer = wait_peer_health(fabric_3, observer, 1, "Lost", timeout=8.0)
        assert peer is not None, f"node {observer} does not see node 1 as Lost"

    # Briefly wait for the fabric to stabilize in 2-node operation.
    time.sleep(0.5)

    # 2. Second failure — the last remaining node must go to failsafe.
    assert fabric_3.diag.shutdown(2)
    assert wait_node_died(fabric_3, 2, timeout=5.0)

    # The single remaining node must not be allowed to keep running alone.
    assert wait_node_died(fabric_3, 0, timeout=8.0), (
        "the lone survivor should have gone to failsafe "
        "(Fix B: no unilateral exclude)"
    )
