"""
T38 — Single-frame rendezvous robustness.

Setup:     3 stable nodes.
Injection: fake_phase_header <target> count=1 (a single frame carrying
           node_state_wire = 0x08 / ErrorManagement), immediately
           cleared so the target resumes sending normally. Repeated
           over ATTEMPTS independent iterations.

Merge note:
    This scenario used to be two, T38 and T39, framing the identical
    injection as an accidental bit flip and as a deliberate attack
    respectively. Same call, same parameters, same assertions, so they
    could and did disagree within a single run on pure timing. They
    are merged here: the distinction is not observable from the
    receiving nodes' point of view, and one scenario that quantifies
    the effect is better evidence than two that contradict each other.

What actually happens (measured, see below):
    The forged header does NOT drive the fabric into the safe state.
    It costs a healthy node its membership.

    1. Node 2 emits ONE frame with a corrupted node_state_wire while
       its payload is perfectly fine.
    2. Every node that processes that frame mid-phase sets
       `peer_in_error_seen` and takes the rendezvous into EM. This
       pulls the phase boundary forward, ahead of the deadline.
    3. In EM the attribution asks who has not contributed to the
       current cycle. A node that happens to be a few hundred
       microseconds behind in its own cycle has not contributed YET.
    4. The attribution cannot tell "has not sent yet" from "will not
       send", because the rendezvous skipped the deadline whose whole
       job is to make that distinction. Two reporters are enough, the
       exclusion is confirmed, and the lagging node finds itself
       already excluded when it finally reaches EM.
    5. The remaining two nodes carry on. Safety is untouched. The
       entire redundancy reserve is gone, spent on a node with
       nothing wrong with it.

    Reference run (20 ms cycle, simulated fabric, loaded host):
    five attempts survived with the three nodes entering ShareInputs
    13 to 72 us apart; the sixth cascaded with a spread of 253 us,
    after the victim had overrun one earlier cycle by 2.2 ms and not
    fully caught up. The trigger is ordinary scheduling jitter, an
    order of magnitude above the measured clock-sync error bound but
    well inside what a non-realtime host produces.

Pass criterion:
    All ATTEMPTS iterations leave the fabric fully intact: three
    nodes cycling, no node isolated or failsafe, and no node seeing
    any peer as anything but Alive. The scenario classifies a failure
    as FALSE_EXCLUSION or CASCADE, because the two have very
    different consequences and the old assertions reported the
    first one in the vocabulary of the second.

Known result:
    Expected to fail. It is kept in the catalog as the evidence for
    that gap, not as a regression guard.

Mitigations (see the thesis outlook):
    a) `for_cycle_seq` in the frame header, rendezvous only on a
       cycle match, which kills the stale-frame case outright
    b) multi-frame confirmation: rendezvous only after N EM frames
       inside a short window, which kills the single-frame case
    c) suppress attribution for peers whose contribution window has
       not elapsed yet when EM is entered via rendezvous rather than
       via a deadline -- the direct fix for the mechanism above
    d) authenticated node_state (HMAC over the critical header
       fields), against a deliberately forged header
    e) rate limiting: at most X EM rendezvous per peer per second

Coverage:  rendezvous robustness against an individual forged or
           stale EM header
"""
import time

from harness.assertions import wait_cycles_advance

TARGET = 2
ALL_NODES = (0, 1, 2)

ATTEMPTS = 10
DELIVERY_S = 0.3      # let the forged frame be delivered and processed
PROGRESS_CYCLES = 15  # cycles each node must advance per attempt

_BROKEN_STATES = ("Isolation", "Failsafe")


def _classify(fabric) -> str | None:
    """Describe how the fabric is damaged, or None if it is intact.

    Checked in order of severity so the message names the primary
    effect rather than a downstream symptom.
    """
    # A node that stopped answering at all is gone, not merely excluded.
    dead = [nid for nid in ALL_NODES
            if fabric.diag.get_status(nid, timeout=2.0) is None]
    if dead:
        return f"CASCADE: node(s) {dead} no longer answer on the diag channel"

    states = {nid: fabric.diag.get_status(nid, timeout=2.0) for nid in ALL_NODES}

    failsafe = [nid for nid, s in states.items()
                if s.get("last_failsafe_reason") is not None]
    if failsafe:
        detail = ", ".join(
            f"{nid}={states[nid].get('last_failsafe_reason')}" for nid in failsafe
        )
        return f"CASCADE: failsafe reason set on node(s) {failsafe} ({detail})"

    broken = {nid: s.get("node_state") for nid, s in states.items()
              if s.get("node_state") in _BROKEN_STATES}
    if broken:
        return (
            f"FALSE_EXCLUSION: node(s) {sorted(broken)} left regular "
            f"operation ({broken}) although only node {TARGET}'s header "
            "was forged and its payload was correct"
        )

    # An exclusion is visible from the observers before the victim
    # itself reports Isolation, so check peer health too.
    for observer, s in states.items():
        for peer in s.get("peers", []):
            if peer.get("health") != "Alive":
                return (
                    f"FALSE_EXCLUSION: node {observer} sees peer "
                    f"{peer.get('id')} as {peer.get('health')} after a "
                    "pure header spoof"
                )

    # Everyone healthy: confirm they are also making progress.
    for nid in ALL_NODES:
        if not wait_cycles_advance(
            fabric, nid, n_cycles=PROGRESS_CYCLES, timeout=8.0,
        ):
            return f"CASCADE: node {nid} healthy but not advancing cycles"

    return None


def test_single_frame_rendezvous_robustness(fabric_3):
    survived = 0
    failure: str | None = None

    for attempt in range(1, ATTEMPTS + 1):
        assert fabric_3.diag.fake_phase_header(
            TARGET, count=1, wire_value=8,
        ) is not None, f"injection not staged on attempt {attempt}"

        time.sleep(DELIVERY_S)
        fabric_3.diag.clear(TARGET)

        failure = _classify(fabric_3)
        if failure:
            break
        survived += 1

    print(
        f"\n[T38] single forged EM header: survived {survived}/{ATTEMPTS} "
        f"attempts" + (f", then {failure.split(':')[0]}" if failure else "")
    )

    assert failure is None, (
        f"the fabric did not survive attempt {survived + 1} of {ATTEMPTS} "
        f"after a single forged EM header. {failure}. "
        f"Survived {survived} attempt(s) before that. "
        "The rendezvous pulls the phase boundary ahead of the deadline, "
        "so EM attribution cannot distinguish a peer that has not sent "
        "yet from one that never will. See the module docstring."
    )
