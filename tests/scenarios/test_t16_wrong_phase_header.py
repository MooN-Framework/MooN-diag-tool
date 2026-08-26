"""
T16 — Frame with a wrong phase header.

Setup:     3 stable nodes.
Injection: Node 2 sends its node_state_wire as the value of
           `NodeState::ErrorManagement` (0x08) instead of its real
           phase, for 20 frames.

Observed behaviour (a positive robustness finding):
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

Test assertion:
    Verify that at least one observer actually triggered the
    rendezvous (log evidence). This confirms that
    `InjectFakePhaseHeader` works and that the header is being
    evaluated. The fabric's recovery behaviour is the positive
    side-finding.
"""
from harness.diag import NODE_STATE_WIRE

TARGET = 2
RENDEZVOUS_TIMEOUT_S = 6.0


def test_wrong_phase_header(fabric_3):
    fake = NODE_STATE_WIRE["ErrorManagement"]
    resp = fabric_3.diag.fake_phase_header(TARGET, count=20, wire_value=fake)
    assert resp is not None, "fake_phase_header injection not staged"

    # At least one observer must have triggered the rendezvous. This
    # is the direct effect of the forged phase header and the only
    # observable effect in normal operation -- the fabric recovers
    # afterwards by design.
    triggered = False
    for observer in (0, 1):
        node = fabric_3.nodes[observer]
        if node.wait_for_log(
            r"peer already in ErrorManagement, rendezvous",
            timeout=RENDEZVOUS_TIMEOUT_S,
        ):
            triggered = True
            break

    assert triggered, (
        "No rendezvous on nodes 0 or 1 within "
        f"{RENDEZVOUS_TIMEOUT_S}s -- the injection isn't working, or "
        "the header isn't being evaluated at all"
    )
