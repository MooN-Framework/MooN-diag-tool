"""
T1 — Silent after input.

Setup:     3 stable nodes (fabric_3).
Injection: silent <target> 1  (drop-results + drop-acks combined).
Expected:  The target sends its input, then goes silent. From the
           peers' point of view, the target is now indistinguishable
           from a crash or a Byzantine-silent node -- this is not an
           identifiable divergence case. Peers time out in
           ShareResult, enter EM, exclude the target, and briefly
           continue with 2 nodes. The target itself keeps running in
           parallel (it still hears the peers), lands in the next
           cycle's CycleSync without peer beacons, and goes through
           CycleSyncTimeout → EM → StateTimeout → Failsafe, then
           broadcasts GoFailsafe. That takes all three down.

Semantics: silent-but-not-crashed is Byzantine. Consensus can no
longer distinguish "I am blind" from "the peer is Byzantine". The
conservative reaction is failsafe.
"""
from harness.assertions import wait_node_died

TARGET = 2


def test_silent_after_input(fabric_3):
    assert fabric_3.diag.silent(TARGET, 1), "silent injection not acknowledged"

    for nid in fabric_3.nodes:
        assert wait_node_died(fabric_3, nid, timeout=15.0), (
            f"node {nid} should reach failsafe after the silent injection"
        )
