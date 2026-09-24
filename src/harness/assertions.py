"""
Assertion helpers for the test scenarios.

We check success two ways:
1. Status via GetStatus (primary, structured).
2. Log-pattern matching (fallback, once a node has entered failsafe).

Both are wrapped in wait-functions because the system is asynchronous
-- we can't know exactly when an injection takes effect.
"""
from __future__ import annotations

import time
from typing import Optional

from .fabric import Fabric


def wait_peer_health(
    fabric: Fabric,
    observer_id: int,
    target_peer_id: int,
    expected_health: str,
    timeout: float = 5.0,
) -> Optional[dict]:
    """
    Waits until node `observer_id` reports peer `target_peer_id` with
    `expected_health`. `expected_health` is "Alive", "Lost",
    "Probation", etc. -- the string as it appears in StatusResponse.peers.

    Returns the peer dict on match, None on timeout.
    """
    assert fabric.diag
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = fabric.diag.get_status(observer_id, timeout=1.0)
        if status:
            for peer in status.get("peers", []):
                if peer["id"] == target_peer_id and peer["health"] == expected_health:
                    return peer
        time.sleep(0.1)
    return None


def wait_node_state(
    fabric: Fabric,
    node_id: int,
    expected_state: str,
    timeout: float = 5.0,
) -> Optional[dict]:
    """Waits until a node is running in a specific NodeState."""
    assert fabric.diag
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = fabric.diag.get_status(node_id, timeout=1.0)
        if status and status.get("node_state") == expected_state:
            return status
        time.sleep(0.1)
    return None


def wait_node_died(fabric: Fabric, node_id: int, timeout: float = 5.0) -> bool:
    """
    Waits until a node stops responding.

    Uses GetStatus on fabric.diag rather than Node.is_running()
    (a process poll): if the node doesn't answer within the timeout,
    it's considered dead. This works identically for simulated AND
    hardware nodes (harness.hw_node.RemoteNode.is_running() does now
    also offer a real SSH liveness check, but GetStatus is
    deliberately kept as the source of truth here -- the state we
    actually care about is "no longer responding within the running
    system", not "the OS process no longer exists").
    """
    assert fabric.diag
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if fabric.diag.get_status(node_id, timeout=0.5) is None:
            return True
        time.sleep(0.1)
    return False


def wait_cycles_advance(
    fabric: Fabric,
    node_id: int,
    n_cycles: int,
    timeout: float = 10.0,
) -> bool:
    """
    Waits until the node's `current_seq` has grown by at least
    n_cycles. Useful for confirming that the fabric keeps running
    after an injection (i.e. it didn't hang in failsafe).
    """
    assert fabric.diag
    initial = fabric.diag.get_status(node_id, timeout=2.0)
    if not initial:
        return False
    start_seq = initial["current_seq"]

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = fabric.diag.get_status(node_id, timeout=1.0)
        if status and status["current_seq"] >= start_seq + n_cycles:
            return True
        time.sleep(0.1)
    return False


def any_node_reached_failsafe(fabric: Fabric, timeout: float = 5.0) -> Optional[int]:
    """
    Checks whether any node has entered failsafe (via log pattern).
    Returns the node_id of the first node to reach failsafe, or None.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for nid, node in fabric.nodes.items():
            if node.wait_for_log(r"failsafe entered", timeout=0.2):
                return nid
        time.sleep(0.1)
    return None


def assert_exclusion_confirmed(fabric: Fabric, target_peer_id: int) -> None:
    """
    Hard assertion: at least one node has logged
    'peer excluded peer_id=X'. Use `wait_peer_health` for the soft
    version.
    """
    pat = rf"peer excluded peer_id={target_peer_id}"
    found = False
    for node in fabric.nodes.values():
        if node.wait_for_log(pat, timeout=2.0):
            found = True
            break
    assert found, f"no node logged exclusion of peer {target_peer_id}"


def wait_last_failsafe_reason(
    fabric: Fabric,
    node_id: int,
    expected_reason: int,
    timeout: float = 8.0,
) -> bool:
    """
    Waits until the node's `StatusResponse.last_failsafe_reason`
    reports the expected `expected_reason` (the u8 value of the
    FailsafeReason enum from the Rust framework). Use the
    `FAILSAFE_REASON` constant from `harness.diag` for symbolic names.

    Important: once a node enters failsafe, the process exits. It
    then no longer answers get_status -- so we poll aggressively
    (100ms interval, short get_status timeout) to reliably catch the
    window between mark_failsafe and process exit.

    Returns True on match, False on timeout or if the node dies
    before a response with the reason set arrives.
    """
    assert fabric.diag
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = fabric.diag.get_status(node_id, timeout=0.15)
        if status is not None:
            reason = status.get("last_failsafe_reason")
            if reason == expected_reason:
                return True
        # No sleep -- on a fast collapse we need to fit as many poll
        # attempts as possible between mark_failsafe and process exit.
    return False


def assert_transition_sequence_present(
    fabric: Fabric,
    node_id: int,
    needle: list[tuple[str, str, str]],
    timeout: float = 5.0,
) -> bool:
    """
    Checks whether the node's `recent_transitions` ring buffer
    contains the given subsequence (each entry = `(from, event, to)`)
    in exactly this order. Not necessarily contiguous -- only that
    the entries occur in this order. Useful for verifying "did the
    node go through the expected FSM path".

    Polls aggressively until match or timeout. Returns True on match,
    False otherwise.
    """
    assert fabric.diag
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status = fabric.diag.get_status(node_id, timeout=0.15)
        if status is not None:
            transitions = [
                (t["from"], t["event"], t["to"])
                for t in status.get("recent_transitions", [])
            ]
            # Subsequence match: walk needle, for each entry look for
            # the next matching position in transitions.
            idx = 0
            for entry in transitions:
                if idx >= len(needle):
                    break
                if entry == needle[idx]:
                    idx += 1
            if idx == len(needle):
                return True
        # No sleep -- poll aggressively.
    return False


_DIAG_LOG_MARKERS = (
    "transition from=", "failsafe entered", "peer excluded",
    "isolat", "exclusion", "deadline exceeded",
)


def describe_node(fabric: Fabric, node_id: int, log_lines: int = 12) -> str:
    """Human-readable snapshot of what a node is actually doing, for
    assertion messages. A failed wait_node_state() only says "not in
    state X"; this says which state it IS in (or that it no longer
    answers), its last failsafe reason, its recent FSM transitions and
    the last relevant lines of its own log. Works for simulated and
    hardware nodes alike."""
    out: list[str] = [f"--- node {node_id} ---"]
    status = fabric.diag.get_status(node_id, timeout=1.5) if fabric.diag else None
    if status is None:
        out.append("GetStatus: no answer (process exited, e.g. after Failsafe)")
    else:
        out.append(
            f"GetStatus: node_state={status.get('node_state')} "
            f"last_failsafe_reason={status.get('last_failsafe_reason')} "
            f"current_seq={status.get('current_seq')}"
        )
        peers = ", ".join(f"{p.get('id')}:{p.get('health')}" for p in status.get("peers", []))
        if peers:
            out.append(f"peers: {peers}")
        trans = status.get("recent_transitions", [])[-8:]
        if trans:
            out.append("recent_transitions: " + " | ".join(
                f"{t.get('from')} -{t.get('event')}-> {t.get('to')}" for t in trans
            ))
    node = fabric.nodes.get(node_id)
    if node is not None:
        relevant = [ln for ln in node.log_lines
                    if any(m in ln.lower() for m in _DIAG_LOG_MARKERS)]
        if relevant:
            out.append(f"last {min(log_lines, len(relevant))} relevant log lines:")
            out.extend("  " + ln for ln in relevant[-log_lines:])
    return "\n".join(out)


def first_node_logging(
    fabric: Fabric, node_ids, pattern: str, timeout: float = 6.0,
) -> Optional[int]:
    """Id of the first node in `node_ids` whose log matches `pattern`
    within `timeout`, else None.

    For timeout-coverage scenarios: a dropped frame makes EVERY observer
    wait for it, but which observer's own deadline fires first is a
    matter of how far apart the nodes run. In simulation they are
    microseconds apart, on the Pi cluster milliseconds, and the first
    node into EM can take the others along before their own deadline
    fires. Pinning the assertion to one fixed observer therefore tests
    node scheduling, not the edge. Asking "did any observer take it"
    tests the edge."""
    deadline = time.monotonic() + timeout
    while True:
        for nid in node_ids:
            if fabric.nodes[nid].wait_for_log(pattern, timeout=0):
                return nid
        if time.monotonic() >= deadline:
            return None
        time.sleep(0.05)
