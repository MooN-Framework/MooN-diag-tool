"""
T38 — Stale EM-frame rendezvous robustness.

Setup:     3 stable nodes.

Attack/bug scenario:
    1. Node 2 briefly does a fake_phase_header (count=1) → a single EM
       frame goes out, after which it resumes sending normal frames.
    2. Between sending and processing on the receiver, OS socket
       buffers can deliver the frame with a delay (typically up to
       100ms, given stale_frame_threshold_ms).
    3. If nodes 0/1 process the EM frame WHILE a normal phase is
       running, they set `peer_in_error_seen=true` and trigger the
       rendezvous.
    4. But node 2 is not actually in EM -- it sends no exclusion
       proposals. Nodes 0/1 in EM expect votes from node 2 (which is
       non-candidate if nobody proposed it, or candidate if both
       propose it due to the EM rendezvous -- either is possible
       depending on the exact sequence).
    5. Critical point: `handle_error_management` goes DIRECTLY to
       Failsafe(QuorumLost) on `PhaseOutcome::Timeout`, with no
       buffer.

Check:
    After the trigger -- did the cluster survive (survivor cycles keep
    advancing, no failsafe reason set)?

    - PASS: the cluster is robust against a single-frame rendezvous
      trigger
    - FAIL: a documented robustness problem -- any bit flip or delayed
      frame can drag the cluster into failsafe

Mitigations if FAIL:
    a) `for_cycle_seq` in the frame header, rendezvous only if the
       cycle matches (prevents stale matches)
    b) Multi-frame confirmation: rendezvous only after N EM frames
       within a short window (prevents a Byzantine single-frame
       trigger)
    c) Lower `stale_frame_threshold_ms` against `peer_in_error_seen`
       (only count EM frames younger than N ms)

Coverage:  Robustness of the rendezvous mechanism against stale/
           spurious EM frames.
"""
import time

from harness.assertions import wait_cycles_advance

TARGET = 2
SURVIVORS = (0, 1)


def test_stale_em_frame_robustness(fabric_3):
    # A SINGLE forged EM frame, then clear it immediately so the
    # target resumes sending normally afterwards. Realistic scenario:
    # a bit flip on a single frame, not repeated.
    assert fabric_3.diag.fake_phase_header(TARGET, count=1, wire_value=8) is not None

    # Wait briefly for the forged frame to be delivered and processed.
    time.sleep(0.3)

    # Explicitly clear the target -- it now sends normally again.
    fabric_3.diag.clear(TARGET)

    # Core question: do the survivors survive? If they're locked in EM
    # and cascade into failsafe there, their cycles stop advancing.
    for survivor in SURVIVORS:
        advanced = wait_cycles_advance(
            fabric_3, survivor, n_cycles=15, timeout=8.0,
        )
        if not advanced:
            # Failsafe cascade detected. Document the current state
            # for debugging.
            status = fabric_3.diag.get_status(survivor, timeout=2.0)
            state = status.get("node_state") if status else "unreachable"
            reason = (
                status.get("last_failsafe_reason") if status else None
            )
            raise AssertionError(
                f"Cluster cascaded after a single stale/spurious EM "
                f"frame: survivor {survivor}'s advance check failed. "
                f"Current state: {state}, "
                f"last_failsafe_reason: {reason}. "
                f"→ Robustness problem: see docstring for mitigations."
            )

    # Optional: if a survivor is locked in EM without failsafe, the
    # cycle progress check above would still pass. But we want it to
    # actually be back in normal operation.
    for survivor in SURVIVORS:
        status = fabric_3.diag.get_status(survivor, timeout=2.0)
        assert status is not None, f"survivor {survivor} unreachable"
        assert status.get("last_failsafe_reason") is None, (
            f"survivor {survivor} has last_failsafe_reason="
            f"{status.get('last_failsafe_reason')} set -- should stay "
            "healthy after a stale EM frame"
        )
