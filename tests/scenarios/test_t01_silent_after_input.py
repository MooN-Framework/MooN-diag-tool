"""
T1 — Silent after input.

Setup:     3 stable nodes (fabric_3).
Injection: silent <target> 1  (drop-results + drop-acks combined).
Expected:  The target sends its input, then goes silent. From the
           peers' point of view it is now indistinguishable from a
           crash or a Byzantine-silent node. They time out in
           ShareResult, enter EM and exclude it. The target follows
           them into EM via the rendezvous, learns that it is the
           excluded one and goes to Isolation. The remaining two carry
           on in 2-node operation.

Semantics: silent-but-not-crashed is Byzantine, and consensus cannot
tell "I am blind" from "the peer is Byzantine". Excluding the odd node
out is the conservative reaction as long as (n - k) >= minimum.

Why this is no longer a triple failsafe:
    The target used to sit out the full deadline of every phase before
    noticing anything, by which time the peers had already finished
    their EM round and excluded it. It then found an empty EM phase,
    ran into `StateTimeout`, went to Failsafe and broadcast
    GoFailsafe, which took the healthy pair with it.

    The rendezvous flag is now part of the completion predicate of the
    four in-cycle phases, so the target leaves the phase the moment a
    peer signals ErrorManagement. It reaches EM in time to hear both
    exclusion proposals against itself, recognises them via
    `self_excluded_by_peers` and goes to Isolation. Same detection,
    same exclusion, but the healthy pair survives -- the availability
    that 2oo3 exists for. Compare T7 and T25, which have had these
    semantics for the divergence cases all along.
"""
from harness.assertions import (
    wait_cycles_advance,
    wait_node_state,
    wait_peer_health,
)

TARGET = 2
SURVIVOR_CYCLES = 10


def test_silent_after_input(fabric_3):
    assert fabric_3.diag.silent(TARGET, 1), "silent injection not acknowledged"

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