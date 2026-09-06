"""
T3 — Silent before input.

Like T1, except the target swallows its own input entirely instead of
its results and acks. The peers time out in ShareInputs, enter EM and
exclude it; the target follows via the rendezvous and goes to
Isolation. The remaining two carry on in 2-node operation.

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


def test_silent_before_input(fabric_3):
    assert fabric_3.diag.drop_inputs(TARGET, 1) is not None, (
        "drop_inputs injection not acknowledged"
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