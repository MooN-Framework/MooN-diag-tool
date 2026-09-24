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
from harness.assertions import assert_transition_sequence_present, first_node_logging

TARGET = 2


def test_share_result_timeout(fabric_3):
    assert fabric_3.diag.drop_results(TARGET, count=1) is not None

    # Any observer: which one's own deadline fires first depends on
    # node scheduling, see harness.assertions.first_node_logging.
    observer = first_node_logging(
        fabric_3, [n for n in sorted(fabric_3.nodes) if n != TARGET],
        r'phase deadline exceeded phase="share_result"', timeout=6.0,
    )
    assert observer is not None, "no observer logged a share_result timeout"
    print(f"\n[T27] share_result deadline taken by observer {observer}", flush=True)
    node = fabric_3.nodes[observer]

    assert node.wait_for_log(
        r"transition from=ShareResult event=ShareResultTimeout to=ErrorManagement",
        timeout=2.0,
    ), "the observer should log the ShareResultTimeout → EM transition"

    _ = assert_transition_sequence_present(
        fabric_3, observer,
        [("ShareResult", "ShareResultTimeout", "ErrorManagement")],
        timeout=1.5,
    )
