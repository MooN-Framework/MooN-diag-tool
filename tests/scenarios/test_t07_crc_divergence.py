"""
T07 — CRC divergence (updated).

Setup:     3 stable nodes.
Injection: fake-crc <target> 1.
Expected:  The target sends CRC = 0xDEADBEEF, peers see the
           divergence. They determine the correct CRC by majority
           (the 2 healthy peers agree) and propose the target for
           exclusion. The target detects this via
           self_excluded_by_peers in EM and transitions to
           SelfExcluded → Isolation.
           Peers continue running in 2-node operation.

Change from earlier behaviour: not all three nodes die anymore.
Isolation is the correct reaction to a single divergent node in 2oo3
as long as (n - k) >= minimum.
"""
from harness.assertions import (
    wait_cycles_advance,
    wait_node_state,
    wait_peer_health,
)

TARGET = 2


def test_crc_divergence(fabric_3):
    assert fabric_3.diag.fake_crc(TARGET, 1), "injection not acknowledged"

    # The target goes into Isolation (the process keeps running, but isolated).
    target_status = wait_node_state(fabric_3, TARGET, "Isolation", timeout=8.0)
    assert target_status is not None, (
        f"target {TARGET} should be in Isolation"
    )

    # Peers see the target as Lost and keep running.
    survivors = [nid for nid in fabric_3.nodes if nid != TARGET]
    for survivor in survivors:
        peer = wait_peer_health(fabric_3, survivor, TARGET, "Lost", timeout=8.0)
        assert peer is not None, (
            f"node {survivor} does not see target {TARGET} as Lost"
        )

    assert wait_cycles_advance(fabric_3, survivors[0], n_cycles=5, timeout=8.0), (
        "peers did not keep running after the CRC divergence"
    )
