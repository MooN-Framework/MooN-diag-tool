"""
T9 — Simultaneous double failure in 2oo4.

Setup:     4 stable nodes (fabric_4).
Injection: shutdown of 2 nodes in quick succession (effectively
           simultaneous from the system's point of view).
Expected:  Both are excluded. The two remaining nodes keep running
           with a reduced quorum (nominal=4, minimum=2 → OK).
"""
from harness.assertions import (
    wait_cycles_advance,
    wait_node_died,
    wait_peer_health,
)

TARGETS = [2, 3]


def test_double_fault_2oo4(fabric_4):
    # Send both shutdowns back to back.
    for t in TARGETS:
        assert fabric_4.diag.shutdown(t), f"shutdown for node {t} not acknowledged"

    for t in TARGETS:
        assert wait_node_died(fabric_4, t, timeout=5.0), (
            f"node {t} did not exit"
        )

    survivors = [nid for nid in fabric_4.nodes if nid not in TARGETS]
    for survivor in survivors:
        for target in TARGETS:
            peer = wait_peer_health(fabric_4, survivor, target, "Lost", timeout=10.0)
            assert peer is not None, (
                f"node {survivor} does not see target {target} as Lost"
            )

    # The two remaining nodes must keep running.
    assert wait_cycles_advance(fabric_4, survivors[0], n_cycles=5, timeout=8.0)
