"""
T8 — Cycle skew.

Setup:     3 stable nodes.
Injection: cycle-delay <target> <ms> 1  (extra sleep in ReadInputs).
Expected:  The target starts its cycle late. If the delay stays under
           the in-cycle deadline offsets, the rendezvous path catches
           it up -- the fabric keeps running, no failsafe. If the
           delay is significantly larger, the node must be excluded.

We test the "not too much skew" path: 3 ms extra delay against a
share_inputs_offset of 5 ms. This should just barely work without an
exclusion, and the node should stay Alive.
"""
from harness.assertions import wait_cycles_advance, wait_peer_health

TARGET = 2
DELAY_MS = 3


def test_cycle_skew_small_recovers(fabric_3):
    assert fabric_3.diag.cycle_delay(TARGET, DELAY_MS, 1), (
        "cycle-delay injection not acknowledged"
    )

    # After the one delayed cycle, the target should still be Alive.
    # We wait a few cycles and then check.
    survivors = [nid for nid in fabric_3.nodes if nid != TARGET]
    assert wait_cycles_advance(fabric_3, survivors[0], n_cycles=5, timeout=8.0)

    # Goal: from the other nodes' point of view, peer 2 is Alive.
    for survivor in survivors:
        # The peer should NOT be Lost.
        peer_lost = wait_peer_health(fabric_3, survivor, TARGET, "Lost", timeout=1.0)
        assert peer_lost is None, (
            f"target {TARGET} was wrongly excluded after a small skew"
        )
