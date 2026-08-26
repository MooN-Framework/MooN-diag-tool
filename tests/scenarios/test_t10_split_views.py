"""
T10 — Split views (asymmetric observation).

Setup:     3 stable nodes.
Injection: Node 2 discards all incoming frames from node 0
           (`InjectDropFromPeer` with mask 0b0000_0001).
Expected:  Nodes 0 and 1 detect node 2 as deviating and exclude it
           via EM consensus (`peer excluded peer_id=2`).

Follow-on effect by framework design (not a bug, intended by the
safety case):
    Node 2, excluded from 0/1's point of view, loses quorum and goes
    into failsafe. The resulting GoFailsafe broadcast pulls 0 and 1
    down with it -- fail-stop is the conservative semantics this
    framework uses. The time window between the Lost marking and the
    system-wide failsafe is around ~50 ms, so we verify via logs
    rather than status polling.
"""
TARGET = 2
BLOCKED_PEER = 0
EXCLUSION_TIMEOUT_S = 8.0


def test_split_views(fabric_3):
    peers_mask = 1 << BLOCKED_PEER
    resp = fabric_3.diag.drop_from_peer(TARGET, peers_mask)
    assert resp is not None, "drop_from_peer injection not staged"

    for observer in (0, 1):
        observer_node = fabric_3.nodes[observer]
        assert observer_node.wait_for_log(
            rf"peer excluded peer_id={TARGET}", timeout=EXCLUSION_TIMEOUT_S
        ), (
            f"observer {observer} did not exclude node {TARGET} "
            f"within {EXCLUSION_TIMEOUT_S}s"
        )
