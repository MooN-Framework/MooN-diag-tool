"""
T13 — Rejoin after isolation.

Setup:     3 stable nodes.
Sequence:  1. Shut the target down cleanly.
           2. Wait until peers see it as Lost.
           3. Restart the process (same config).
           4. Wait for the readmit event (Lost -> Probation), then
              for the promotion event (Probation -> Alive).
Expected:  The target rejoins the cluster and ends up Alive again.
"""
from harness.assertions import wait_peer_health

TARGET = 2
OBSERVER = 0


def test_rejoin(fabric_3):
    # 1. Shut the target down cleanly.
    assert fabric_3.diag.shutdown(TARGET) is not None, (
        "shutdown injection not acknowledged"
    )

    # 2. Peers must see it as Lost.
    peer = wait_peer_health(fabric_3, OBSERVER, TARGET, "Lost", timeout=8.0)
    assert peer is not None, f"observer {OBSERVER} does not see target {TARGET} as Lost"

    # 3. Restart the process.
    fabric_3.restart_node(TARGET)

    # 4a. Readmit event in the observer's log (Lost -> Probation).
    #     Log-based because the probation window at default timings
    #     (10 cycles * 20 ms = 200 ms) is only about two of
    #     wait_peer_health's polling attempts wide. The nominal pause
    #     is 100 ms, but each attempt additionally costs a GetStatus
    #     round trip (1.0 s timeout), so the effective resolution is
    #     both larger than 100 ms and not bounded from above. Catching
    #     a 200 ms window with it is a coin flip, so we don't try.
    observer_node = fabric_3.nodes[OBSERVER]
    assert observer_node.wait_for_log(
        rf"peer readmitted.*peer_id={TARGET}", timeout=15.0
    ), f"observer {OBSERVER} did not readmit target {TARGET} (Lost->Probation)"

    # 4b. Promotion event (Probation -> Alive).
    assert observer_node.wait_for_log(
        r"peers promoted from Probation to Alive", timeout=10.0
    ), f"target {TARGET} was not promoted from Probation to Alive"

    # 5. Sanity check: the end state is actually Alive.
    peer = wait_peer_health(fabric_3, OBSERVER, TARGET, "Alive", timeout=5.0)
    assert peer is not None, f"target {TARGET} is not Alive after rejoin"
