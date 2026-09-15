"""
T06 — Result divergence.

Setup:     3 stable nodes.
Injection: Node 2 sends a tampered BrakeResult (large distance shift
           + emergency flip) for `count` cycles.
Expected:  Nodes 0 and 1 agree on their shared value, identify node 2
           via find_dissenters, and propose it for exclusion. After
           EM they see it as Lost.
"""
from harness.assertions import wait_peer_health

TARGET = 2


def test_result_divergence(fabric_3):
    assert fabric_3.diag.corrupt_result(TARGET, count=3) is not None

    for observer in (0, 1):
        peer = wait_peer_health(fabric_3, observer, TARGET, "Lost", timeout=8.0)
        assert peer is not None, (
            f"observer {observer} did not mark node {TARGET} as Lost"
        )
