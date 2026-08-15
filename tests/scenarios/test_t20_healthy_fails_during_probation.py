"""
T20 — Ausfall eines gesunden Nodes waehrend ein anderer in Probation ist
      => Failsafe.

Setup:     3 Nodes stabil (Node 0, 1, 2).
Sequenz:
    1. Target-Node (2) shutdown -> Peers sehen ihn als Lost.
    2. Target-Node (2) neu starten -> re-admitted, laeuft im Probation-
       Fenster (health = Probation).
    3. Waehrend Node 2 noch in Probation ist: einen der beiden "gesunden"
       Nodes (0) shutdownen.

Erwartet:
    Mit nur einem verifiziert-gesunden Node (Node 1) und einem in
    Probation ist das Quorum nicht mehr sicher erreichbar. Fix B (kein
    unilateraler exclude) verhindert dass Node 1 alleine weitermacht:
    er muss in Failsafe gehen.

Timing:
    Wir muessen den Ausfall von Node 0 exakt IM Probation-Fenster
    treffen. Probation-Dauer = probation_cycles (Default 10) * cycle_ms
    (Default 20) = ~200 ms. Deshalb: wait_for_log auf das readmit-
    Event (Lost -> Probation) und SOFORT danach shutdown ausloesen —
    genau der gleiche Weg den T13 (rejoin) nutzt.
"""
from harness.assertions import (
    any_node_reached_failsafe,
    wait_node_died,
    wait_peer_health,
)


PROBATION_TARGET = 2   # Node der in Probation gehen soll
FAIL_TARGET = 0        # gesunder Node der WAEHREND Probation ausfaellt
OBSERVER = 1           # verbleibender Node — muss Failsafe erreichen


def test_healthy_node_fails_during_probation(fabric_3):
    # 1. Probation-Target sauber runterfahren.
    assert fabric_3.diag.shutdown(PROBATION_TARGET) is not None, (
        "shutdown injection nicht bestaetigt"
    )
    assert wait_node_died(fabric_3, PROBATION_TARGET, timeout=5.0)

    # 2. Peers sehen ihn als Lost.
    peer = wait_peer_health(fabric_3, OBSERVER, PROBATION_TARGET,
                            "Lost", timeout=8.0)
    assert peer is not None, (
        f"observer {OBSERVER} sieht target {PROBATION_TARGET} nicht als Lost"
    )

    # 3. Prozess neu starten -> readmit -> Probation.
    fabric_3.restart_node(PROBATION_TARGET)

    # 4. Auf das readmit-Log-Ereignis am Observer warten. Sobald das
    #    passiert IST der Node in Probation. Log-Weg statt GetStatus
    #    weil das Probation-Fenster zu kurz zum Polling ist (siehe T13).
    observer_node = fabric_3.nodes[OBSERVER]
    assert observer_node.wait_for_log(
        rf"peer readmitted.*peer_id={PROBATION_TARGET}", timeout=15.0
    ), (
        f"observer {OBSERVER} hat target {PROBATION_TARGET} nicht "
        f"readmitted (Lost->Probation)"
    )

    # 5. SOFORT den gesunden Node ausschalten. Wir sind jetzt im
    #    Probation-Fenster (~200 ms). Der shutdown-Command selbst wartet
    #    nicht auf Bestaetigung — er feuert und rennt.
    fabric_3.diag.shutdown(FAIL_TARGET)

    # 6. Der Ausfall muss real sein.
    assert wait_node_died(fabric_3, FAIL_TARGET, timeout=5.0), (
        f"node {FAIL_TARGET} process not exited"
    )

    # 7. Erwartung: irgendein verbleibender Node erreicht Failsafe.
    #    Wir pruefen bewusst nicht *welcher* (Observer oder das re-
    #    startete Probation-Target) — Fix B garantiert nur dass kein
    #    Node alleine weiterlaeuft.
    failed_id = any_node_reached_failsafe(fabric_3, timeout=15.0)
    assert failed_id is not None, (
        "kein Node in Failsafe — Fabric haette bei "
        "1 gesunder + 1 Probation-Node nicht weiterlaufen duerfen"
    )
