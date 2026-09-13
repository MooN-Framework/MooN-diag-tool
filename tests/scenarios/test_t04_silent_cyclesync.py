"""
T4 — Silent in CycleSync.

Setup:     3 stable nodes (fabric_3).
Injection: drop_cyclesync <target> 1 (the target suppresses its own
           CycleSync beacon for one cycle, everything else stays
           untouched).
Expected:  The peers wait for the target's beacon, hit the CycleSync
           deadline and enter EM, where the missing-peer attribution
           has two independent reporters and the exclusion is
           confirmed. The target keeps receiving peer frames, so it
           picks up the peers' EM state -- but CycleSync itself has no
           rendezvous edge, so the latch is only consumed by the next
           phase handler (ShareInputs). It therefore reaches EM one
           phase later, recognises the confirmed exclusion against
           itself and goes to Isolation. The remaining two carry on in
           2-node operation.

Difference from T29:
    T29 injects the same fault but asserts only the state-machine edge
    (CycleSync, CycleSyncTimeout) -> ErrorManagement on one observer.
    T04 asserts the system-level outcome instead: who ends up
    excluded, and that the healthy pair survives without a GoFailsafe
    cascade.

Difference from T01/T03:
    Same outcome, but the fault hits the phase *before* any payload is
    exchanged. The target never contributes to the cycle at all, so
    the exclusion rests purely on the beacon attribution rather than
    on a missing input or result.
"""
from harness.assertions import (
    wait_cycles_advance,
    wait_node_state,
    wait_peer_health,
)

TARGET = 2
SURVIVOR_CYCLES = 10


def test_silent_cyclesync(fabric_3):
    assert fabric_3.diag.drop_cyclesync(TARGET, 1) is not None, (
        "drop_cyclesync injection not acknowledged"
    )

    # The target is excluded and isolates itself instead of failing safe.
    target_status = wait_node_state(fabric_3, TARGET, "Isolation", timeout=10.0)
    assert target_status is not None, (
        f"target {TARGET} should end up in Isolation, not Failsafe"
    )

    # The survivors see it as Lost and keep cycling.
    survivors = [nid for nid in fabric_3.nodes if nid != TARGET]
    for survivor in survivors:
        peer = wait_peer_health(fabric_3, survivor, TARGET, "Lost", timeout=8.0)
        assert peer is not None, (
            f"survivor {survivor} should mark node {TARGET} as Lost"
        )

    for survivor in survivors:
        assert wait_cycles_advance(
            fabric_3, survivor, n_cycles=SURVIVOR_CYCLES, timeout=10.0,
        ), (
            f"survivor {survivor} should keep running {SURVIVOR_CYCLES} cycles "
            "in 2-node operation"
        )

    for survivor in survivors:
        status = fabric_3.diag.get_status(survivor, timeout=2.0)
        assert status is not None, f"survivor {survivor} unreachable"
        assert status.get("last_failsafe_reason") is None, (
            f"survivor {survivor} has last_failsafe_reason="
            f"{status.get('last_failsafe_reason')} -- a GoFailsafe cascade "
            "from the excluded node would show up here"
        )