"""
T27 — ShareResultTimeout (timeout coverage).

Setup:     3 stable nodes.
Injection: drop_results <target> for one cycle (ONLY results, not
           acks -- the difference from T01's silent injection).
Trigger:   The target sends its input, peers agree. Peers wait in
           ShareResult for the target's result → `ShareResultTimeout`.

Focus:     Log assertion primary, recent_transitions is a bonus.

Coverage:  (ShareResult, ShareResultTimeout) → ErrorManagement
"""
from harness.assertions import assert_transition_sequence_present

TARGET = 2
OBSERVER = 0


def test_share_result_timeout(fabric_3):
    assert fabric_3.diag.drop_results(TARGET, count=1) is not None

    node = fabric_3.nodes[OBSERVER]
    assert node.wait_for_log(
        r'phase deadline exceeded phase="share_result"',
        timeout=6.0,
    ), "the observer should log a share_result timeout"

    assert node.wait_for_log(
        r"transition from=ShareResult event=ShareResultTimeout to=ErrorManagement",
        timeout=2.0,
    ), "the observer should log the ShareResultTimeout → EM transition"

    _ = assert_transition_sequence_present(
        fabric_3, OBSERVER,
        [("ShareResult", "ShareResultTimeout", "ErrorManagement")],
        timeout=1.5,
    )
