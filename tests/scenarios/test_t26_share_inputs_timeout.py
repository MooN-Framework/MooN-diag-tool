"""
T26 — ShareInputsTimeout (timeout coverage).

Setup:     3 stable nodes.
Injection: drop_inputs <target> for one cycle.
Trigger:   Peers wait in ShareInputs for the target's input. The
           target sends nothing. Peers hit the phase deadline →
           `ShareInputsTimeout` → EM.

Focus:     Verify that exactly this timeout edge fires. The log
           assertion is primary (deterministic from the on-disk log);
           recent_transitions is an optional bonus.

Coverage:  (ShareInputs, ShareInputsTimeout) → ErrorManagement
"""
from harness.assertions import assert_transition_sequence_present

TARGET = 2
OBSERVER = 0


def test_share_inputs_timeout(fabric_3):
    assert fabric_3.diag.drop_inputs(TARGET, count=3) is not None

    node = fabric_3.nodes[OBSERVER]
    assert node.wait_for_log(
        r'phase deadline exceeded phase="share_inputs"',
        timeout=6.0,
    ), "the observer should log a share_inputs timeout"

    assert node.wait_for_log(
        r"transition from=ShareInputs event=(ShareInputsTimeout|PeerInError) to=ErrorManagement",
        timeout=2.0,
    ), (
        "the observer should log the ShareInputsTimeout → EM transition "
        "(or a PeerInError rendezvous if the other observer was faster "
        "and an EM frame was already exchanged)"
    )

    _ = assert_transition_sequence_present(
        fabric_3, OBSERVER,
        [("ShareInputs", "ShareInputsTimeout", "ErrorManagement")],
        timeout=1.5,
    )
