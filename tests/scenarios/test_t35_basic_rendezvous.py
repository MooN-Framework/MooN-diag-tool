"""
T35 — Basic PeerInError rendezvous.

Setup:     3 stable nodes.
Injection: fake_phase_header <target> with wire_value=0x08 (EM) for
           5 outgoing frames.
Trigger:   The target sends a forged node_state_wire=ErrorManagement
           for 5 frames. The other nodes receive it → set
           `peer_in_error_seen=true` during ingest → on the next
           `take_peer_in_error()` in one of the phase handlers
           (ShareInputs, ShareResult, SendAck, CRC),
           `StateEvent::PeerInError` is emitted → rendezvous into EM.

Focus:     Prove that the PeerInError rendezvous mechanism works at
           all. Which of the four phases triggers the rendezvous is
           timing-dependent -- we accept any of them.

Coverage:  (ShareInputs | ShareResult | SendAck | SystemStateCrcExchange,
            PeerInError) → ErrorManagement
"""
NODE_EM = 2       # target node
NODE_A = 0        # observer, affected by the rendezvous
NODE_B = 1        # observer, affected by the rendezvous


def test_basic_peer_in_error_rendezvous(fabric_3):
    # fake_phase_header already exists in the framework (the T16
    # command). wire_value=8 = NodeState::ErrorManagement.
    assert fabric_3.diag.fake_phase_header(NODE_EM, count=5, wire_value=8) is not None, (
        "fake_phase_header injection not acknowledged"
    )

    # At least one of the two observers must log a PeerInError
    # transition. The phase can be any of the four.
    rendezvous_pattern = (
        r"transition from=(ShareInputs|ShareResult|SendAck|SystemStateCrcExchange) "
        r"event=PeerInError to=ErrorManagement"
    )

    triggered_on = None
    for observer in (NODE_A, NODE_B):
        node = fabric_3.nodes[observer]
        if node.wait_for_log(rendezvous_pattern, timeout=6.0):
            triggered_on = observer
            break

    assert triggered_on is not None, (
        f"No observer triggered a PeerInError rendezvous. "
        f"The peer_in_error_seen ingest latch doesn't seem to react to "
        f"forged EM headers, or the phase handlers aren't consuming "
        f"the latch correctly."
    )
