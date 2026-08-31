"""
T15 — Publisher disagreement (updated).

Setup:     3 stable nodes.
Injection: divergent-publisher <target> 1.
Expected:  The target sends an ack with the wrong publisher_candidate.
           From the peers' point of view, the three picks bucket into
           2 matching + 1 divergent. A majority can be determined →
           peers propose the target. The target itself sees consensus
           (its own pick + peer picks look fine from its own view),
           lands in EM via the peer_in_error rendezvous, and is
           marked SelfExcluded → Isolation via self_excluded_by_peers.
           Peers continue running in 2-node operation.

Change from earlier behaviour: publisher divergence with an
identifiable majority is no longer treated as a Byzantine case. Only
when no majority can be determined at all (e.g. 3 different picks in
a 3-node cluster) does it remain a failsafe case.
"""
from harness.assertions import (
    wait_cycles_advance,
    wait_node_state,
    wait_peer_health,
)

TARGET = 2


def test_publisher_disagreement(fabric_3):
    assert fabric_3.diag.divergent_publisher(TARGET, 1), (
        "injection not acknowledged"
    )

    target_status = wait_node_state(fabric_3, TARGET, "Isolation", timeout=8.0)
    assert target_status is not None, (
        f"target {TARGET} should be in Isolation"
    )

    survivors = [nid for nid in fabric_3.nodes if nid != TARGET]
    for survivor in survivors:
        peer = wait_peer_health(fabric_3, survivor, TARGET, "Lost", timeout=8.0)
        assert peer is not None

    assert wait_cycles_advance(fabric_3, survivors[0], n_cycles=5, timeout=8.0)
