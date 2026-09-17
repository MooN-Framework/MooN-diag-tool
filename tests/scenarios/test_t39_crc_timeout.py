"""
T39 — CrcTimeout (timeout coverage and single-fault tolerance).

Setup:     3 stable nodes.
Injection: drop_crc <target> for one cycle. The target still sends its
           input, result and ack, and only withholds its system-state
           CRC.

Trigger:   The observers wait in SystemStateCrcExchange for the
           target's CRC. They hit the cycle-anchored crc_offset
           deadline → `CrcTimeout` → EM. Because the target acked this
           cycle and at least one CRC (the other observer's) arrived,
           each observer proposes the target for exclusion.

Expected:  Both observers exclude the target and keep cycling without
           a failsafe reason. The target never reaches the CRC
           deadline itself: it has both observer CRCs, publishes,
           times out in CycleSync because the observers are already
           in EM, and picks up their proposals in its own EM round.
           It recognises its exclusion and goes to Isolation.

Why this scenario exists:
    Before the worst-case analysis a CRC timeout went straight to
    Failsafe, so a single node that fell silent between its ack and
    its CRC took a full 2oo3 fabric down with it. The edge now routes
    through EM like every other in-cycle timeout. This scenario pins
    both the edge and the system-level consequence.

Rendezvous:
    The CRC phase does not raise the rendezvous latch, so an observer
    cannot be pulled into EM early by the other observer's or the
    target's EM header. Unlike T26, the edge is therefore asserted
    strictly.

Coverage:  (SystemStateCrcExchange, CrcTimeout) → ErrorManagement
"""
from harness.assertions import (
    assert_transition_sequence_present,
    wait_cycles_advance,
    wait_node_state,
    wait_peer_health,
)

TARGET = 2
OBSERVERS = (0, 1)
SURVIVOR_CYCLES = 10


def test_crc_timeout(fabric_3):
    assert fabric_3.diag.drop_crc(TARGET, count=1) is not None, (
        "drop_crc injection not acknowledged"
    )

    # (1) The edge itself, on every observer.
    for observer in OBSERVERS:
        node = fabric_3.nodes[observer]
        assert node.wait_for_log(
            r'phase deadline exceeded phase="system_state_crc"',
            timeout=6.0,
        ), f"observer {observer} should log a system_state_crc timeout"
        assert node.wait_for_log(
            r"transition from=SystemStateCrcExchange event=CrcTimeout "
            r"to=ErrorManagement",
            timeout=2.0,
        ), (
            f"observer {observer} should log the CrcTimeout → EM "
            "transition, not a direct failsafe"
        )

    _ = assert_transition_sequence_present(
        fabric_3, OBSERVERS[0],
        [("SystemStateCrcExchange", "CrcTimeout", "ErrorManagement")],
        timeout=1.5,
    )

    # (2) Single-fault tolerance: the target is voted out, not the
    #     whole fabric.
    for observer in OBSERVERS:
        peer = wait_peer_health(fabric_3, observer, TARGET, "Lost", timeout=8.0)
        assert peer is not None, (
            f"observer {observer} should mark node {TARGET} as Lost"
        )

    target_status = wait_node_state(fabric_3, TARGET, "Isolation", timeout=8.0)
    assert target_status is not None, (
        f"target {TARGET} should end up in Isolation, not Failsafe"
    )

    # (3) The majority keeps running and did not fail safe.
    for observer in OBSERVERS:
        assert wait_cycles_advance(
            fabric_3, observer, n_cycles=SURVIVOR_CYCLES, timeout=10.0,
        ), (
            f"observer {observer} should keep running {SURVIVOR_CYCLES} "
            "cycles in 2-node operation"
        )
        status = fabric_3.diag.get_status(observer, timeout=2.0)
        assert status is not None, f"observer {observer} unreachable"
        assert status.get("last_failsafe_reason") is None, (
            f"observer {observer} has last_failsafe_reason="
            f"{status.get('last_failsafe_reason')} -- a single missing CRC "
            "must not take the fabric down"
        )
