"""
T21 — A probation node fails during resynchronization
      => isolation, the two healthy nodes keep running.

Setup:     3 stable nodes.
Sequence:
    1. Shut the target node down -> peers see it as Lost.
    2. Restart the target node -> readmit -> probation window.
    3. WHILE in the probation phase: fail the target node again
       (second shutdown).

Expected:
    - The target is marked as Lost/excluded again by the two healthy
      nodes (not promoted).
    - The 2 remaining healthy nodes stay operational (2oo3 with 2
      Alive peers).
    - NO failsafe -- unlike T20 (where a healthy node fails), losing a
      probation node is tolerable.
"""
import time

from harness.assertions import (
    wait_cycles_advance,
    wait_node_died,
    wait_node_state,
    wait_peer_health,
)


PROBATION_TARGET = 2
SURVIVOR_A = 0
SURVIVOR_B = 1


def test_probation_node_fails_during_resync(fabric_3):
    # 1. Shut the target down.
    assert fabric_3.diag.shutdown(PROBATION_TARGET) is not None
    assert wait_node_died(fabric_3, PROBATION_TARGET, timeout=5.0)

    # 2. Peers see it as Lost.
    for observer in (SURVIVOR_A, SURVIVOR_B):
        peer = wait_peer_health(fabric_3, observer, PROBATION_TARGET,
                                "Lost", timeout=8.0)
        assert peer is not None, (
            f"observer {observer} does not see target {PROBATION_TARGET} as Lost"
        )

    # 3. Restart the target.
    fabric_3.restart_node(PROBATION_TARGET)

    # 4. Wait for the readmit event (Lost -> Probation) -- log-based
    #    as in T13/T20, since the probation window is too short for
    #    GetStatus polling.
    survivor_node = fabric_3.nodes[SURVIVOR_A]
    assert survivor_node.wait_for_log(
        rf"peer readmitted.*peer_id={PROBATION_TARGET}", timeout=15.0
    ), (
        f"survivor {SURVIVOR_A} did not readmit target {PROBATION_TARGET} "
        f"(Lost->Probation)"
    )

    # 5. Second shutdown WHILE in probation.
    fabric_3.diag.shutdown(PROBATION_TARGET)
    assert wait_node_died(fabric_3, PROBATION_TARGET, timeout=5.0), (
        f"node {PROBATION_TARGET} process not exited on the second shutdown"
    )

    # 6. Both healthy nodes must see it as Lost again.
    for observer in (SURVIVOR_A, SURVIVOR_B):
        node = fabric_3.nodes[observer]
        assert node.wait_for_log(
            rf"peer readmitted.*peer_id={PROBATION_TARGET}", timeout=15.0
        ), f"observer {observer} did not readmit target {PROBATION_TARGET} again"

    # 7. The two healthy nodes MUST keep cycling -- no failsafe. We
    #    wait for actual cycle progression, which is the hard proof
    #    that the fabric stayed operational.
    for survivor in (SURVIVOR_A, SURVIVOR_B):
        assert wait_cycles_advance(
            fabric_3, survivor, n_cycles=5, timeout=8.0
        ), f"survivor {survivor} did not complete 5 further cycles"

    # 8. Sanity: neither of the two accidentally ended up in failsafe.
    for survivor in (SURVIVOR_A, SURVIVOR_B):
        state = wait_node_state(fabric_3, survivor, "Failsafe", timeout=0.5)
        assert state is None, (
            f"survivor {survivor} unexpectedly ended up in failsafe"
        )
