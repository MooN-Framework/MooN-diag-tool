"""
T29 — CycleSyncTimeout (timeout coverage).

Setup:     3 stable nodes.
Injection: drop_cyclesync <target> for one cycle.
Trigger:   Peers wait in CycleSync for the target's cycle-sync beacon.
           The target sends none → `CycleSyncTimeout`.

Focus:     Log assertion primary, recent_transitions is a bonus.

Coverage:  (CycleSync, CycleSyncTimeout) → ErrorManagement
"""
from harness.assertions import assert_transition_sequence_present

TARGET = 2
OBSERVER = 0


def test_cycle_sync_timeout(fabric_3):
    assert fabric_3.diag.drop_cyclesync(TARGET, count=1) is not None

    node = fabric_3.nodes[OBSERVER]
    assert node.wait_for_log(
        r'phase deadline exceeded phase="cycle_sync"',
        timeout=6.0,
    ), "the observer should log a cycle_sync timeout"

    assert node.wait_for_log(
        r"transition from=CycleSync event=CycleSyncTimeout to=ErrorManagement",
        timeout=2.0,
    ), "the observer should log the CycleSyncTimeout → EM transition"

    _ = assert_transition_sequence_present(
        fabric_3, OBSERVER,
        [("CycleSync", "CycleSyncTimeout", "ErrorManagement")],
        timeout=1.5,
    )
