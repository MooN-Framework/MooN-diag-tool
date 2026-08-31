"""
T39 — Byzantine single-frame rendezvous attack.

Setup:     3 stable nodes.

Attack scenario:
    A single Byzantine/faulty peer sends ONE single frame with a
    corrupted `node_state_wire = 0x08 (ErrorManagement)`. The payload
    is normal (input/result/...), only the header byte is wrong. The
    peer itself does not actually go into EM afterwards -- it keeps
    sending normally.

    Proof-of-concept for the attack surface:
    - No CBI (Byzantine agreement) in the rendezvous path
    - No multi-frame confirmation
    - No cycle-alignment check
    → One bit is (theoretically) enough to drag the cluster into
      failsafe

    Difference from T38:
    T38 models an unintentional fault (bit flip, delayed frame). T39
    models a deliberate attack.

    Both use the SAME mechanism (fake_phase_header count=1) because,
    from the target nodes' point of view, it's indistinguishable
    whether the frame was manipulated intentionally or accidentally.
    The difference lies in the semantics of the assertion.

Assertion:
    The cluster survives (the attack has no effect). If FAIL:
    → attack surface documented, mitigations described in the T38
      docstring, plus additionally:
    d) Authenticated node_state (HMAC over critical header fields)
    e) Rate limiting: max X EM rendezvous per peer per second

Coverage:  Rendezvous security against individual Byzantine frames.
"""
import time

from harness.assertions import wait_cycles_advance

BYZANTINE = 2
SURVIVORS = (0, 1)


def test_byzantine_single_frame_attack(fabric_3):
    # A single forged frame. Analogous to T38 but with different
    # assertion semantics.
    assert fabric_3.diag.fake_phase_header(BYZANTINE, count=1, wire_value=8) is not None

    time.sleep(0.3)
    fabric_3.diag.clear(BYZANTINE)

    # Does the cluster survive the attack?
    survivors_healthy = True
    failure_details = []
    for survivor in SURVIVORS:
        if not wait_cycles_advance(
            fabric_3, survivor, n_cycles=15, timeout=8.0,
        ):
            survivors_healthy = False
            status = fabric_3.diag.get_status(survivor, timeout=2.0)
            failure_details.append(
                f"survivor {survivor}: state="
                f"{status.get('node_state') if status else 'unreachable'}, "
                f"reason="
                f"{status.get('last_failsafe_reason') if status else '?'}"
            )
            continue
        status = fabric_3.diag.get_status(survivor, timeout=2.0)
        if status and status.get("last_failsafe_reason") is not None:
            survivors_healthy = False
            failure_details.append(
                f"survivor {survivor} has last_failsafe_reason="
                f"{status.get('last_failsafe_reason')} set"
            )

    assert survivors_healthy, (
        f"Byzantine single-frame attack succeeded -- the cluster is not "
        f"robust against a single forged EM header. Details: "
        f"{failure_details}. → attack surface confirmed, "
        f"mitigations described in the T38/T39 docstrings."
    )
