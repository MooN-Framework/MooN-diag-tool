"""
T16 — Frame with a wrong phase header.

Setup:     3 stable nodes.
Injection: Node 2 sends its node_state_wire as the value of
           `NodeState::ErrorManagement` (0x08) instead of its real
           phase, for 20 frames.

Expected behaviour (a positive robustness finding):
    Nodes 0 and 1 detect the ErrorManagement header and set their
    rendezvous flag `peer_in_error_seen`. At the end of the phase
    currently running, they transition to EM via `PeerInError`.
    However, NO exclusion proposal is raised against node 2 there,
    because node 2 delivered its payloads (input, result) correctly
    despite the spoofed header, so there is no attribution basis for
    an exclusion. The aggregated exclusion votes are empty, EM
    concludes with `StateOk`, and the fabric returns to CycleSync.

    Effect: a brief EM excursion per forged frame consumed, then
    recovery. Once the 20 fakes are exhausted, the fabric continues
    unchanged.

Thesis finding:
    The rendezvous mechanism is deliberately more tolerant than the
    exclusion mechanism: it pulls peers into EM *preemptively*, but
    requires further evidence for an actual exclusion (an attribution
    counter built from genuinely missing frames). This makes an
    isolated header spoof harmless -- a fault-tolerant peer would not
    be permanently excluded because of a single faulty rendezvous.
    This costs one cycle of latency and is the acceptable price for
    fast rendezvous synchronization.

Test assertions:
    1. At least one observer actually triggered the rendezvous (log
       evidence). Confirms that `InjectFakePhaseHeader` works and
       that the header is being evaluated at all.
    2. No observer logged an exclusion of the target, and neither
       observer ever marked it as Lost. This is the tolerance claim
       above, and it is the assertion that would break first if the
       attribution ever started counting spoofed headers.
    3. After the 20 forged frames are exhausted, both observers keep
       cycling and carry no failsafe reason. This is the recovery
       claim.

    Assertions 2 and 3 were added after the thesis review: the
    scenario previously asserted only (1) while the surrounding
    documentation claimed all three, so the recovery and no-exclusion
    properties were described but never actually verified.

Relation to T35:
    T35 injects the same fault with 5 instead of 20 frames and
    asserts the state-machine edge (which of the four rendezvous
    phases fired). T16 asserts the system-level consequence: that the
    spoof is survivable and does not cost the target its membership.
"""
from harness.assertions import wait_cycles_advance, wait_peer_health
from harness.diag import NODE_STATE_WIRE

TARGET = 2
OBSERVERS = (0, 1)
FAKE_FRAMES = 20
RENDEZVOUS_TIMEOUT_S = 6.0

# 20 forged frames at the default 20 ms cycle take ~400 ms to be
# consumed. Wait comfortably past that before checking for recovery,
# so the "fabric is healthy again" assertions do not race the tail of
# the injection.
DRAIN_S = 3.0
SURVIVOR_CYCLES = 10


def test_wrong_phase_header(fabric_3):
    fake = NODE_STATE_WIRE["ErrorManagement"]
    resp = fabric_3.diag.fake_phase_header(TARGET, count=FAKE_FRAMES, wire_value=fake)
    assert resp is not None, "fake_phase_header injection not staged"

    # (1) The rendezvous must actually have been triggered. Without
    #     this, the two negative assertions below would pass on a
    #     no-op injection and prove nothing.
    triggered = False
    for observer in OBSERVERS:
        node = fabric_3.nodes[observer]
        if node.wait_for_log(
            r"peer already in ErrorManagement, rendezvous",
            timeout=RENDEZVOUS_TIMEOUT_S,
        ):
            triggered = True
            break

    assert triggered, (
        f"no rendezvous on nodes {OBSERVERS} within "
        f"{RENDEZVOUS_TIMEOUT_S}s -- the injection isn't working, or "
        "the header isn't being evaluated at all"
    )

    # (2) No exclusion. A spoofed header alone must not be an
    #     attribution basis, since the target's payloads were fine.
    for observer in OBSERVERS:
        node = fabric_3.nodes[observer]
        excluded = node.wait_for_log(
            rf"peer excluded peer_id={TARGET}", timeout=DRAIN_S,
        )
        assert not excluded, (
            f"observer {observer} excluded node {TARGET} after a pure "
            "header spoof -- the attribution counter must not credit a "
            "forged phase header, only genuinely missing frames"
        )

    for observer in OBSERVERS:
        lost = wait_peer_health(fabric_3, observer, TARGET, "Lost", timeout=1.0)
        assert lost is None, (
            f"observer {observer} marked node {TARGET} as Lost after a "
            "pure header spoof"
        )

    # (3) Recovery. The fabric must be back in regular cycle operation
    #     once the forged frames are exhausted.
    for observer in OBSERVERS:
        assert wait_cycles_advance(
            fabric_3, observer, n_cycles=SURVIVOR_CYCLES, timeout=10.0,
        ), (
            f"observer {observer} did not advance {SURVIVOR_CYCLES} "
            "cycles after the injection was exhausted -- it is likely "
            "stuck in ErrorManagement"
        )

        status = fabric_3.diag.get_status(observer, timeout=2.0)
        assert status is not None, f"observer {observer} unreachable"
        assert status.get("last_failsafe_reason") is None, (
            f"observer {observer} has last_failsafe_reason="
            f"{status.get('last_failsafe_reason')} -- the header spoof "
            "should not have produced a failsafe reason"
        )
