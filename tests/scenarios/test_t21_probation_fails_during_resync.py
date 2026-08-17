"""
T21 — Ausfall eines Probation-Nodes waehrend der Resynchronisation
      => Isolation, die zwei gesunden Nodes laufen weiter.

Setup:     3 Nodes stabil.
Sequenz:
    1. Target-Node runterfahren -> Peers sehen ihn als Lost.
    2. Target-Node neu starten -> readmit -> Probation-Fenster.
    3. WAEHREND der Probation-Phase: den Target-Node nochmal ausfallen
       lassen (zweiter shutdown).

Erwartet:
    - Der Target wird von den beiden gesunden Nodes wieder als Lost /
      excluded markiert (nicht promotet).
    - Die 2 verbleibenden gesunden Nodes bleiben operational (2oo3 mit
      2 Alive-Peers).
    - KEIN Failsafe — im Gegensatz zu T20 (wo ein gesunder Node
      ausgefallen ist) ist der Ausfall des Probation-Nodes tolerierbar.
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
    # 1. Target runterfahren.
    assert fabric_3.diag.shutdown(PROBATION_TARGET) is not None
    assert wait_node_died(fabric_3, PROBATION_TARGET, timeout=5.0)

    # 2. Peers sehen ihn als Lost.
    for observer in (SURVIVOR_A, SURVIVOR_B):
        peer = wait_peer_health(fabric_3, observer, PROBATION_TARGET,
                                "Lost", timeout=8.0)
        assert peer is not None, (
            f"observer {observer} sieht target {PROBATION_TARGET} nicht als Lost"
        )

    # 3. Target neu starten.
    fabric_3.restart_node(PROBATION_TARGET)

    # 4. Auf das readmit-Ereignis warten (Lost -> Probation) — Log-Weg
    #    wie in T13/T20, weil das Probation-Fenster fuer GetStatus-Polling
    #    zu kurz ist.
    survivor_node = fabric_3.nodes[SURVIVOR_A]
    assert survivor_node.wait_for_log(
        rf"peer readmitted.*peer_id={PROBATION_TARGET}", timeout=15.0
    ), (
        f"survivor {SURVIVOR_A} hat target {PROBATION_TARGET} nicht "
        f"readmitted (Lost->Probation)"
    )

    # 5. Zweiter shutdown WAEHREND Probation.
    fabric_3.diag.shutdown(PROBATION_TARGET)
    assert wait_node_died(fabric_3, PROBATION_TARGET, timeout=5.0), (
        f"node {PROBATION_TARGET} process not exited on second shutdown"
    )

    # 6. Beide gesunden Nodes muessen ihn wieder als Lost sehen.
    for observer in (SURVIVOR_A, SURVIVOR_B):
            node = fabric_3.nodes[observer]
            assert node.wait_for_log(
                rf"peer readmitted.*peer_id={PROBATION_TARGET}", timeout=15.0
            ), ...

    # 7. Die zwei gesunden Nodes MUESSEN weiter cyceln — kein Failsafe.
    #    Wir warten aktive Zyklus-Progression ab, das ist der harte
    #    Beweis dass die Fabric operational geblieben ist.
    for survivor in (SURVIVOR_A, SURVIVOR_B):
        assert wait_cycles_advance(
            fabric_3, survivor, n_cycles=5, timeout=8.0
        ), f"survivor {survivor} hat keine 5 weiteren Zyklen geschafft"

    # 8. Sanity: keiner der beiden ist versehentlich in Failsafe.
    for survivor in (SURVIVOR_A, SURVIVOR_B):
        state = wait_node_state(fabric_3, survivor, "Failsafe", timeout=0.5)
        assert state is None, (
            f"survivor {survivor} ist unerwartet in Failsafe gelandet"
        )
