"""
T36 — Reliable PeerInError rendezvous (reliability).

Setup:     3 stable nodes.
Sequence:  Over 5 iterations, each:
             1. Trigger fake_phase_header <target> with count=3
             2. Wait briefly (300ms) -- enough cycles for 1-2
                rendezvous triggers at a 20ms cycle duration
             3. clear_injection on all nodes
             4. Wait until the fabric is operational again -- the
                cluster must return to normal operation after every
                rendezvous
             After each iteration: check that at least one new
             PeerInError log entry has appeared.

Focus:     Reliability of the rendezvous mechanism -- not just
           "happens sometimes" but "happens reproducibly". Important
           for the safety argument: the rendezvous path must react
           reliably to transient EM frames, or the cluster could
           cascade apart into an inconsistent state of timeouts.

Coverage:  Reliability of (ShareInputs|ShareResult|SendAck|CRC,
            PeerInError) → ErrorManagement
"""
import time


ITERATIONS = 5
TARGET = 2
OBSERVERS = (0, 1)

RENDEZVOUS_LOG = (
    r"transition from=(ShareInputs|ShareResult|SendAck|SystemStateCrcExchange) "
    r"event=PeerInError to=ErrorManagement"
)


def _count_rendezvous_lines(fabric) -> int:
    """Total PeerInError rendezvous logs across both observers."""
    import re
    pat = re.compile(RENDEZVOUS_LOG)
    total = 0
    for obs in OBSERVERS:
        node = fabric.nodes[obs]
        lines = node.log_lines  # snapshot of the current log lines
        total += sum(1 for line in lines if pat.search(line))
    return total


def test_reliable_peer_in_error_rendezvous(fabric_3):
    # Baseline: before the first injection there should be no
    # rendezvous lines in the log (the fabric is freshly stable).
    prev_count = _count_rendezvous_lines(fabric_3)

    for iteration in range(1, ITERATIONS + 1):
        # Trigger
        assert fabric_3.diag.fake_phase_header(
            TARGET, count=3, wire_value=8,
        ) is not None, f"iteration {iteration}: injection not acknowledged"

        # Wait briefly so the forged frames go out and the rendezvous
        # had a chance to fire on at least one observer.
        time.sleep(0.3)

        # Settle the cluster back down (if the injection were still
        # active, every subsequent send would re-arm the latch and the
        # cluster would never settle).
        fabric_3.diag.clear(TARGET)
        fabric_3.diag.clear(OBSERVERS[0])
        fabric_3.diag.clear(OBSERVERS[1])

        # Wait until the cluster is operational again -- every
        # rendezvous goes through EM, then StateOk → CycleSync →
        # normal operation.
        assert fabric_3.wait_operational(timeout=5.0), (
            f"iteration {iteration}: cluster did not recover after "
            "the rendezvous"
        )

        # Check that at least one new rendezvous log entry appeared.
        curr_count = _count_rendezvous_lines(fabric_3)
        new_lines = curr_count - prev_count
        assert new_lines >= 1, (
            f"iteration {iteration}: no new PeerInError rendezvous "
            f"log entry (was {prev_count}, now {curr_count})"
        )
        prev_count = curr_count
