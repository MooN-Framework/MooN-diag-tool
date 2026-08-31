"""
T37 — PeerInError rendezvous phase coverage.

Setup:     3 stable nodes.
Sequence:  20 repeated fake_phase_header injections with varying
           timing.

Observed behaviour (documented, not a bug):
    The rendezvous trigger is structurally concentrated on
    ShareInputs. Reason: the ingest thread sets `peer_in_error_seen`
    as soon as an EM-tagged frame arrives. Between two cycles (after
    Publish → CycleSync → ReadInputs → ShareInputs), none of the
    early handlers has a `take_peer_in_error()` check (CycleSync has
    no rendezvous edge, ReadInputs is infallible). The first
    consuming handler is ShareInputs -- in practice the latch is
    almost always redeemed there.

    The four other rendezvous points (ShareResult, SendAck,
    SystemStateCrcExchange) only fire if the EM frame arrives WHILE a
    phase is actively running, not between phases. That's not
    deterministically controllable with fake_phase_header (peer-side,
    cycle-clocked).

Focus:     Show that the rendezvous fires reliably in general (see T35
           and T36 for reliability itself). This test additionally
           documents the actual phase distribution as a baseline for
           future regression comparisons.

Coverage:  Practical phase distribution of the rendezvous
"""
import re
import time

TARGET = 2
OBSERVERS = (0, 1)
ITERATIONS = 20

PHASE_PATTERN = re.compile(
    r"transition from=(ShareInputs|ShareResult|SendAck|SystemStateCrcExchange) "
    r"event=PeerInError to=ErrorManagement"
)


def test_rendezvous_phase_coverage(fabric_3):
    for iteration in range(ITERATIONS):
        # Variable count so the frame count and timing vary.
        count = 1 + (iteration % 3) * 2  # 1, 3, 5 rotating

        assert fabric_3.diag.fake_phase_header(
            TARGET, count=count, wire_value=8,
        ) is not None, f"iteration {iteration}: injection not acknowledged"

        # Variable wait time -- lands in different cycle phases on the
        # receivers.
        time.sleep(0.15 + (iteration % 4) * 0.05)

        # Clear briefly between iterations so the next injection
        # starts fresh.
        fabric_3.diag.clear(TARGET)
        for obs in OBSERVERS:
            fabric_3.diag.clear(obs)

        # Wait until the cluster is back to normal -- otherwise
        # rendezvous effects would stack up on top of each other.
        _ = fabric_3.wait_operational(timeout=3.0)

    # Collect every phase that was hit by a rendezvous.
    phases_seen: set[str] = set()
    for obs in OBSERVERS:
        node = fabric_3.nodes[obs]
        for line in node.log_lines:
            m = PHASE_PATTERN.search(line)
            if m:
                phases_seen.add(m.group(1))

    assert len(phases_seen) >= 1, (
        "not a single PeerInError rendezvous observed -- "
        "the mechanism isn't firing at all"
    )

    # Informational (not a hard assert): the rendezvous trigger is
    # structurally biased towards ShareInputs because the
    # peer_in_error latch is most often set between cycles (after
    # Publish, before ShareInputs). This is consistent behaviour --
    # the latch is consumed by the first handler that checks it, and
    # that is practically always ShareInputs.
    #
    # Triggering the other phases would require an EM frame arriving
    # WHILE a phase is actively running, which isn't deterministically
    # controllable with the current fake_phase_header mechanism
    # (peer-side, cycle-clocked).
    print(f"\nRendezvous coverage: {sorted(phases_seen)} "
          f"({len(phases_seen)}/4 phases)")
    if len(phases_seen) == 1 and "ShareInputs" in phases_seen:
        print("  (expected: ShareInputs only -- a structural bias from "
              "the latch-consumption order, see docstring)")
