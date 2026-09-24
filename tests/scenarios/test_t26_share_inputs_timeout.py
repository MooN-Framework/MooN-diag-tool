"""
T26 — ShareInputsTimeout (timeout coverage).

Setup:     3 stable nodes.
Injection: drop_inputs <target> for three cycles. Unlike T27-T29,
           which use a single cycle, the window is widened here
           because ShareInputs is the phase the rendezvous latch is
           almost always redeemed in (see T37), so a one-cycle
           injection loses the race too often to be useful.

Trigger:   Peers wait in ShareInputs for the target's input. The
           target sends nothing. Peers hit the phase deadline →
           `ShareInputsTimeout` → EM.

Focus:     Verify that exactly this timeout edge fires. The log
           assertion is primary (deterministic from the on-disk log);
           recent_transitions is an optional bonus.

Known weakness:
    The transition assertion accepts `PeerInError` as well as
    `ShareInputsTimeout`. If the second observer reaches EM first,
    this node arrives via the rendezvous edge instead of its own
    deadline, and the test cannot distinguish the two. T26 therefore
    pins its edge more weakly than T27-T29 do. Tightening it to
    `ShareInputsTimeout` alone would make the scenario flaky on a
    loaded machine rather than more rigorous.

Same injection as T03, which asserts the system-level outcome (who
gets excluded, does the majority survive) instead of the edge.

Coverage:  (ShareInputs, ShareInputsTimeout | PeerInError)
           → ErrorManagement
"""
from harness.assertions import assert_transition_sequence_present, first_node_logging

TARGET = 2


def test_share_inputs_timeout(fabric_3):
    assert fabric_3.diag.drop_inputs(TARGET, count=3) is not None

    # Any observer: which one's own deadline fires first depends on
    # node scheduling, see harness.assertions.first_node_logging.
    observer = first_node_logging(
        fabric_3, [n for n in sorted(fabric_3.nodes) if n != TARGET],
        r'phase deadline exceeded phase="share_inputs"', timeout=6.0,
    )
    assert observer is not None, "no observer logged a share_inputs timeout"
    print(f"\n[T26] share_inputs deadline taken by observer {observer}", flush=True)
    node = fabric_3.nodes[observer]

    assert node.wait_for_log(
        r"transition from=ShareInputs event=(ShareInputsTimeout|PeerInError) to=ErrorManagement",
        timeout=2.0,
    ), (
        "the observer should log the ShareInputsTimeout → EM transition "
        "(or a PeerInError rendezvous if the other observer was faster "
        "and an EM frame was already exchanged)"
    )

    _ = assert_transition_sequence_present(
        fabric_3, observer,
        [("ShareInputs", "ShareInputsTimeout", "ErrorManagement")],
        timeout=1.5,
    )
