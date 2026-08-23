"""
Thread-safe registry of nodes discovered at runtime.

A node is registered as soon as we see its node_id anywhere -- in the
operational multicast or in a diagnostic reply. The registry holds the
last known state per node (derived from operational frames:
node_state_wire, seq_num, last_seen; from diag status: full
StatusResponse).

Signals are dispatched via the Qt wrapper in the UI layer -- the
registry itself has no Qt dependency so it stays unit-testable.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional


@dataclass(slots=True)
class NodeView:
    node_id: int
    first_seen: float
    last_seen: float
    last_state: str = "?"
    last_state_wire: int = 0
    last_seq: int = 0
    last_session: int = 0
    frame_count: int = 0
    diag_status: Optional[dict] = None
    diag_status_ts: float = 0.0
    # Local monotonic time when we first observed the node's CURRENT
    # session_id -- reset whenever session_id changes (the node's
    # process restarted). This is our own observation time, not the
    # node's actual process-start wall clock (we have no way to know
    # that from the wire alone) -- so "process uptime" derived from
    # this is "how long since we last saw this node restart", which is
    # what actually matters for diagnostics. 0.0 = never observed yet.
    session_started_at: float = 0.0


class NodeRegistry:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._nodes: dict[int, NodeView] = {}
        self._on_change_cbs: list[Callable[[NodeView], None]] = []

    def subscribe(self, cb: Callable[[NodeView], None]) -> None:
        with self._lock:
            self._on_change_cbs.append(cb)

    def clear(self) -> None:
        with self._lock:
            self._nodes.clear()

    def ids(self) -> list[int]:
        with self._lock:
            return sorted(self._nodes.keys())

    def snapshot(self) -> list[NodeView]:
        with self._lock:
            return [
                NodeView(**{k: getattr(v, k) for k in v.__slots__})
                for v in sorted(self._nodes.values(), key=lambda n: n.node_id)
            ]

    def get(self, node_id: int) -> Optional[NodeView]:
        with self._lock:
            v = self._nodes.get(node_id)
            if v is None:
                return None
            return NodeView(**{k: getattr(v, k) for k in v.__slots__})

    # ---- update paths -----------------------------------------------------

    def observe_operational(
        self,
        node_id: int,
        state_wire: int,
        state_name: str,
        seq_num: int,
        session_id: int,
    ) -> None:
        now = time.monotonic()
        changed: Optional[NodeView] = None
        with self._lock:
            v = self._nodes.get(node_id)
            if v is None:
                v = NodeView(node_id=node_id, first_seen=now, last_seen=now)
                v.session_started_at = now
                self._nodes[node_id] = v
            elif session_id != v.last_session:
                # New session_id -- the node's process restarted.
                v.session_started_at = now
            v.last_seen = now
            v.last_state = state_name
            v.last_state_wire = state_wire
            v.last_seq = seq_num
            v.last_session = session_id
            v.frame_count += 1
            cbs = list(self._on_change_cbs)
            # The copy below is real work (a getattr per slot) done on
            # whatever thread called this -- often the UDP rx thread, at
            # up to hundreds/thousands of frames/sec with a small
            # cycle_ms. Building it when nothing's actually listening is
            # pure waste that slows down frame ingestion for no reason.
            changed = NodeView(**{k: getattr(v, k) for k in v.__slots__}) if cbs else None
        for cb in cbs:
            try:
                cb(changed)
            except Exception:  # pragma: no cover
                pass

    def observe_diag_status(self, node_id: int, status: dict) -> None:
        now = time.monotonic()
        changed: Optional[NodeView] = None
        with self._lock:
            v = self._nodes.get(node_id)
            if v is None:
                v = NodeView(node_id=node_id, first_seen=now, last_seen=now)
                self._nodes[node_id] = v
            v.last_seen = now
            v.diag_status = status
            v.diag_status_ts = now
            # Pull selected fields from StatusResponse into the "last" cache.
            if "node_state" in status:
                v.last_state = status["node_state"]
            if "current_seq" in status:
                v.last_seq = status["current_seq"]
            if "session_id" in status:
                new_session = status["session_id"]
                if v.session_started_at == 0.0 or new_session != v.last_session:
                    v.session_started_at = now
                v.last_session = new_session
            cbs = list(self._on_change_cbs)
            changed = NodeView(**{k: getattr(v, k) for k in v.__slots__}) if cbs else None
        for cb in cbs:
            try:
                cb(changed)
            except Exception:  # pragma: no cover
                pass
