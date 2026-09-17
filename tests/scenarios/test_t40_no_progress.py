"""
T40 — NoProgress watchdog (persistent header spoof).

Setup:     3 stable nodes.
Injection: fake_phase_header <target> with wire_value=0x08 (EM) for
           an effectively unlimited number of frames.

Trigger:   Every forged header pulls the observers into EM via the
           rendezvous rule. The target's payloads are correct, so no
           exclusion is ever confirmed and every EM round ends in
           StateOk. No cycle reaches a decision any more, while the
           fabric stays formally quorate.

Expected:  After MAX_ROUNDS_WITHOUT_DECISION (5) consecutive EM rounds
           without a decision, a node leaves EM with `NoProgress` and
           goes to failsafe with reason QuorumLost. Its GoFailsafe
           broadcast pulls the remaining nodes along. No node may stay
           in regular operation.

Why this scenario exists:
    Without the watchdog, a persistent spoof (or a persistent
    one-sided frame loss) keeps the fabric looping through EM without
    ever publishing and without ever failing safe: the actuator would
    keep its last value indefinitely. The watchdog is what gives the
    worst-case analysis a bound for "cycles without a decision".
    T16 is the transient counterpart: twenty forged frames force at
    most four rounds and must be survived.

Tolerated side effect:
    A forged header can make EM attribution exclude a healthy node
    (see T38). Such a node ends in Isolation instead of Failsafe. The
    scenario therefore requires every node to be either gone or
    isolated, and at least one node to have taken the NoProgress edge.

Coverage:  (ErrorManagement, NoProgress) → Failsafe
"""
from harness.assertions import wait_node_died
from harness.diag import NODE_STATE_WIRE

TARGET = 2
PERSISTENT_FRAMES = 1_000_000
WATCHDOG_TIMEOUT_S = 6.0


def test_no_progress_watchdog(fabric_3):
    fake = NODE_STATE_WIRE["ErrorManagement"]
    assert fabric_3.diag.fake_phase_header(
        TARGET, count=PERSISTENT_FRAMES, wire_value=fake,
    ) is not None, "fake_phase_header injection not staged"

    # (1) The watchdog edge on at least one node.
    fired_on = None
    for nid, node in fabric_3.nodes.items():
        if node.wait_for_log(
            r"transition from=ErrorManagement event=NoProgress to=Failsafe",
            timeout=WATCHDOG_TIMEOUT_S if fired_on is None else 0.5,
        ):
            fired_on = nid
            break
    assert fired_on is not None, (
        "no node took the NoProgress edge -- a persistent header spoof "
        "must not keep the fabric in EM indefinitely"
    )

    node = fabric_3.nodes[fired_on]
    assert node.wait_for_log(
        r"no decision for too many error-management rounds.*rounds=5 limit=5",
        timeout=1.0,
    ), f"node {fired_on} should log the watchdog with rounds=5 limit=5"
    assert node.wait_for_log(
        r"failsafe entered.*reason=QuorumLost", timeout=1.0,
    ), f"node {fired_on} should report reason=QuorumLost"

    # (2) Nobody stays in regular operation: every node is either gone
    #     (failsafe, directly or via GoFailsafe) or isolated.
    for nid in fabric_3.nodes:
        if wait_node_died(fabric_3, nid, timeout=5.0):
            continue
        status = fabric_3.diag.get_status(nid, timeout=2.0)
        state = status.get("node_state") if status else None
        assert state == "Isolation", (
            f"node {nid} is still answering in state {state} -- it should "
            "have followed the GoFailsafe broadcast or be isolated"
        )
