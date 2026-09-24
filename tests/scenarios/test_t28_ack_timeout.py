"""
T28 — AckTimeout (timeout coverage).

Setup:     3 stable nodes.
Injection: drop_acks <target> for one cycle (ONLY acks, payloads
           still go through).
Trigger:   Peers see the target's input and result. They wait in
           SendAck for the target's ack → `AckTimeout`.

Focus:     Log assertion primary, recent_transitions is a bonus.

Coverage:  (SendAck, AckTimeout) → ErrorManagement
"""
from harness.assertions import assert_transition_sequence_present, first_node_logging

TARGET = 2


def test_ack_timeout(fabric_3):
    assert fabric_3.diag.drop_acks(TARGET, count=1) is not None

    # Any observer: which one's own deadline fires first depends on
    # node scheduling, see harness.assertions.first_node_logging.
    observer = first_node_logging(
        fabric_3, [n for n in sorted(fabric_3.nodes) if n != TARGET],
        r'phase deadline exceeded phase="send_ack"', timeout=6.0,
    )
    assert observer is not None, "no observer logged a send_ack timeout"
    print(f"\n[T28] send_ack deadline taken by observer {observer}", flush=True)
    node = fabric_3.nodes[observer]

    assert node.wait_for_log(
        r"transition from=SendAck event=AckTimeout to=ErrorManagement",
        timeout=2.0,
    ), "the observer should log the AckTimeout → EM transition"

    _ = assert_transition_sequence_present(
        fabric_3, observer,
        [("SendAck", "AckTimeout", "ErrorManagement")],
        timeout=1.5,
    )
