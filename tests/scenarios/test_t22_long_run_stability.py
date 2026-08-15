"""
T22 — Langzeit-Stabilitaetstest.

Setup:     3 Nodes stabil.
Sequenz:
    Fabric fuer DURATION_S Sekunden ungestoert laufen lassen. Periodisch
    (INSPECT_INTERVAL_S) alle Nodes via GetStatus abfragen und die
    Zyklus-Progression pro Node aufzeichnen.

Erwartet:
    - Kein Node stirbt.
    - Kein Node landet in Failsafe / Isolation.
    - Alle Nodes machen ueber das ganze Fenster hinweg gleichmaessige
      Zyklus-Progression (mindestens MIN_CYCLES_PER_WINDOW Zyklen pro
      INSPECT_INTERVAL_S).
    - Peer-Health bleibt fuer alle konstant "Alive" — kein flappender
      Node der zwischendrin auf Lost/Probation faellt.

Ist als `@pytest.mark.slow` markiert und laeuft nur explizit mit
`pytest -m slow`. Fuer CI-Smoke reicht DURATION_S=30. Fuer echte
Langzeit-Studien im Nightly gerne auf ein paar Minuten hochdrehen.
"""
import time

import pytest

from harness.assertions import (
    wait_cycles_advance,
    wait_node_state,
)


DURATION_S = 30.0
INSPECT_INTERVAL_S = 3.0
# Bei cycle_duration_ms=20 sind das ~150 Zyklen pro Intervall. Wir
# fordern nur MIN, um Jitter auf ausgelasteten CI-Maschinen zu tolerieren.
MIN_CYCLES_PER_WINDOW = 50
# Erwartete Zyklen im gesamten Fenster (nur informativ im Report).
_EXPECTED_TOTAL = int(DURATION_S / 0.020)


@pytest.mark.slow
def test_long_run_stability(fabric_3):
    node_ids = sorted(fabric_3.nodes.keys())

    # Baseline seqs erfassen.
    baseline: dict[int, int] = {}
    for nid in node_ids:
        status = fabric_3.diag.get_status(nid, timeout=2.0)
        assert status is not None, f"node {nid} antwortet nicht bei start"
        baseline[nid] = status.get("current_seq", 0)

    start = time.monotonic()
    deadline = start + DURATION_S
    last_seqs: dict[int, int] = dict(baseline)

    report: list[str] = []
    report.append(f"long-run: duration={DURATION_S}s, expected ~{_EXPECTED_TOTAL} cycles/node")

    while time.monotonic() < deadline:
        time.sleep(INSPECT_INTERVAL_S)
        now = time.monotonic() - start

        for nid in node_ids:
            # a) Node laeuft ueberhaupt noch als Prozess.
            assert fabric_3.nodes[nid].is_running(), (
                f"[{now:.1f}s] node {nid} process died"
            )

            # b) GetStatus antwortet + kein Failsafe/Isolation.
            status = fabric_3.diag.get_status(nid, timeout=2.0)
            assert status is not None, (
                f"[{now:.1f}s] node {nid} diag timeout"
            )
            state = status.get("node_state")
            assert state not in ("Failsafe", "Isolation"), (
                f"[{now:.1f}s] node {nid} unexpected state: {state}"
            )

            # c) Zyklus-Progression im letzten Intervall.
            cur_seq = status.get("current_seq", 0)
            delta = cur_seq - last_seqs[nid]
            assert delta >= MIN_CYCLES_PER_WINDOW, (
                f"[{now:.1f}s] node {nid} advanced only {delta} cycles "
                f"in {INSPECT_INTERVAL_S}s (min={MIN_CYCLES_PER_WINDOW})"
            )
            last_seqs[nid] = cur_seq

            # d) Alle Peers sind Alive — keiner flapt.
            for peer in status.get("peers", []):
                assert peer["health"] == "Alive", (
                    f"[{now:.1f}s] node {nid} sees peer {peer['id']} "
                    f"as {peer['health']} (expected Alive)"
                )

        report.append(
            f"  t={now:.1f}s  seqs=" +
            ", ".join(f"{nid}={last_seqs[nid]}" for nid in node_ids)
        )

    # Zusammenfassung.
    total_by_node = {nid: last_seqs[nid] - baseline[nid] for nid in node_ids}
    report.append("summary:")
    for nid in node_ids:
        report.append(
            f"  node {nid}: {total_by_node[nid]} cycles in {DURATION_S:.1f}s "
            f"(~{total_by_node[nid] / DURATION_S:.1f} Hz)"
        )
    print("\n" + "\n".join(report))

    # Sanity: alle Nodes haben ungefaehr gleich viele Zyklen (max Spread
    # 5% zur Sicherstellung dass keiner heimlich langsamer wird).
    counts = list(total_by_node.values())
    lo, hi = min(counts), max(counts)
    spread = (hi - lo) / max(1, hi)
    assert spread <= 0.05, (
        f"cycle count spread too large: min={lo} max={hi} spread={spread:.2%}"
    )
