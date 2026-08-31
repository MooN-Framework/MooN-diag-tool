"""
T12 — Sequential failures in 2oo4.

Setup:     4 stable nodes.
Injection: shutdown 3, wait until stable (cycle with 3 nodes), then
           shutdown 2.
Expected:  Two sequential exclusions. The fabric stays alive with 2
           nodes (nominal=4, minimum=2 → 2 failures are OK).
"""
import time

from harness.assertions import (
    wait_cycles_advance,
    wait_node_died,
    wait_peer_health,
)


def test_sequential_faults_2oo4(fabric_4):
    # 1. First failure.
    assert fabric_4.diag.shutdown(3)
    assert wait_node_died(fabric_4, 3, timeout=5.0)
    for observer in (0, 1, 2):
        peer = wait_peer_health(fabric_4, observer, 3, "Lost", timeout=8.0)
        assert peer is not None

    # Let it stabilize.
    time.sleep(0.5)

    # 2. Second failure.
    assert fabric_4.diag.shutdown(2)
    assert wait_node_died(fabric_4, 2, timeout=5.0)
    for observer in (0, 1):
        peer = wait_peer_health(fabric_4, observer, 2, "Lost", timeout=8.0)
        assert peer is not None

    # The last two must keep running.
    assert wait_cycles_advance(fabric_4, 0, n_cycles=5, timeout=8.0)
