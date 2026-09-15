"""
T25 — Own dissent survives the cluster (validates fix #1).

Setup:     3 stable nodes.
Injection: corrupt_result <target> over multiple cycles.
Expected:  The target sends a tampered BrakeResult over multiple
           cycles. Peers 0 and 1 detect it as a dissenter
           (find_dissenters) and propose it for exclusion. After EM
           they see it as Lost and continue running in 2-node
           operation.

Key point (what sets this apart from T06):
    The CORRUPTED node must not be allowed to take the whole cluster
    down with a GoFailsafe broadcast. Under the old semantics
    (own_dissented → Fault → Failsafe), the remaining two nodes would
    have followed via PeerBroadcast into failsafe and the entire
    fabric would be dead. With fix #1
    (own_dissented → SelfExcluded → Isolation), the cluster stays
    available; the dissenter quietly goes into isolation.

Assertions:
    1. Nodes 0 and 1 mark the target as Lost (the existing T06
       assertion -- a precondition for the injection taking effect).
    2. Nodes 0 and 1 keep running for multiple cycles (current_seq
       grows) -- the cluster isn't dead via PeerBroadcast.
    3. Nodes 0 and 1 have `last_failsafe_reason == None` -- they
       neither entered failsafe nor are on their way there.

This proves:
    - Isolation semantics kick in (target is no longer part of the
      cluster).
    - No failsafe-cascade death of the healthy cluster.
"""
from harness.assertions import (
    wait_cycles_advance,
    wait_peer_health,
)

TARGET = 2
SURVIVOR_CYCLES = 10


def test_own_dissent_survives(fabric_3):
    assert fabric_3.diag.corrupt_result(TARGET, count=5) is not None, (
        "corrupt_result injection not acknowledged"
    )

    survivors = [nid for nid in fabric_3.nodes if nid != TARGET]

    # (1) Peers mark the target as Lost.
    for survivor in survivors:
        peer = wait_peer_health(fabric_3, survivor, TARGET, "Lost", timeout=8.0)
        assert peer is not None, (
            f"observer {survivor} should mark node {TARGET} as Lost"
        )

    # (2) The cluster keeps running -- no PeerBroadcast cascade death.
    for survivor in survivors:
        assert wait_cycles_advance(
            fabric_3, survivor, n_cycles=SURVIVOR_CYCLES, timeout=10.0,
        ), (
            f"survivor {survivor} should keep running {SURVIVOR_CYCLES} "
            "cycles after the dissent -- the cluster was likely destroyed "
            "by a GoFailsafe cascade (fix #1 not effective)"
        )

    # (3) Survivor nodes have NO failsafe reason set.
    for survivor in survivors:
        status = fabric_3.diag.get_status(survivor, timeout=2.0)
        assert status is not None, f"survivor {survivor} unreachable"
        assert status.get("last_failsafe_reason") is None, (
            f"survivor {survivor} unexpectedly has last_failsafe_reason="
            f"{status.get('last_failsafe_reason')} set -- should be "
            "healthy and failsafe-free"
        )
