"""
T29 — CycleSyncTimeout (timeout coverage).

Setup:     3 stable nodes.
Injection: drop_cyclesync <target> for one cycle.
Trigger:   Peers wait in CycleSync for the target's cycle-sync beacon.
           The target sends none → `CycleSyncTimeout`.

Focus:     Log assertion primary, recent_transitions is a bonus.

Coverage:  (CycleSync, CycleSyncTimeout) → ErrorManagement
"""
from harness.assertions import assert_transition_sequence_present, first_node_logging

TARGET = 2


def test_cycle_sync_timeout(fabric_3):
    assert fabric_3.diag.drop_cyclesync(TARGET, count=1) is not None

    # Any observer: which one's own deadline fires first depends on
    # node scheduling, see harness.assertions.first_node_logging.
    observer = first_node_logging(
        fabric_3, [n for n in sorted(fabric_3.nodes) if n != TARGET],
        r'phase deadline exceeded phase="cycle_sync"', timeout=6.0,
    )
    assert observer is not None, "no observer logged a cycle_sync timeout"
    print(f"\n[T29] cycle_sync deadline taken by observer {observer}", flush=True)
    node = fabric_3.nodes[observer]

    assert node.wait_for_log(
        r"transition from=CycleSync event=CycleSyncTimeout to=ErrorManagement",
        timeout=2.0,
    ), "the observer should log the CycleSyncTimeout → EM transition"

    _ = assert_transition_sequence_present(
        fabric_3, observer,
        [("CycleSync", "CycleSyncTimeout", "ErrorManagement")],
        timeout=1.5,
    )
