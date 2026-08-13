"""
Measures cycle timing passively from operational multicast frames.

Every node emits one STATE frame per cycle. We stamp their monotonic
arrival times per node and derive mean/min/max/jitter/overrun stats
without needing log parsing.

Consumers register the measurement as a listener on an existing
OperationalListener (via `attach`). At the end of the window, call
`report()` for the per-node result.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Optional

from .operational_listener import OperationalListener
from .wire_decoder import DecodedFrame, Discriminator


@dataclass(slots=True)
class NodeTimingStats:
    node_id: int
    n_cycles: int = 0
    mean_us: int = 0
    min_us: int = 0
    max_us: int = 0
    jitter_us: int = 0
    overruns: int = 0
    overrun_frac: float = 0.0
    worst_overrun_us: int = 0
    crc_errors: int = 0
    frames_total: int = 0


@dataclass
class CycleMeasurement:
    target_cycle_ms: int
    overrun_tolerance_us: int = 500  # target + tolerance = overrun threshold
    _last_state_ts: dict[int, float] = field(default_factory=dict)
    _deltas_us: dict[int, list[int]] = field(default_factory=dict)
    _crc_errors: dict[int, int] = field(default_factory=dict)
    _frames_total: dict[int, int] = field(default_factory=dict)

    def attach(self, listener: OperationalListener) -> None:
        listener.add_listener(self._on_frame)

    def _on_frame(self, frame: DecodedFrame, ts_mono: float) -> None:
        nid = frame.node_id
        self._frames_total[nid] = self._frames_total.get(nid, 0) + 1
        if not frame.crc_ok:
            self._crc_errors[nid] = self._crc_errors.get(nid, 0) + 1
            return
        # Only STATE frames as cycle heartbeat (one per cycle per node).
        if frame.discriminator != Discriminator.STATE:
            return
        prev = self._last_state_ts.get(nid)
        self._last_state_ts[nid] = ts_mono
        if prev is None:
            return
        delta_us = int((ts_mono - prev) * 1_000_000)
        self._deltas_us.setdefault(nid, []).append(delta_us)

    def report(self) -> dict[int, NodeTimingStats]:
        target_us = self.target_cycle_ms * 1000
        overrun_threshold = target_us + self.overrun_tolerance_us
        out: dict[int, NodeTimingStats] = {}
        # Include nodes we saw at all (even if <2 STATE frames).
        all_ids = set(self._frames_total.keys()) | set(self._deltas_us.keys())
        for nid in sorted(all_ids):
            deltas = self._deltas_us.get(nid, [])
            stats = NodeTimingStats(node_id=nid)
            stats.frames_total = self._frames_total.get(nid, 0)
            stats.crc_errors = self._crc_errors.get(nid, 0)
            if deltas:
                stats.n_cycles = len(deltas)
                stats.mean_us = sum(deltas) // len(deltas)
                stats.min_us = min(deltas)
                stats.max_us = max(deltas)
                stats.jitter_us = stats.max_us - stats.min_us
                overrun_deltas = [d for d in deltas if d > overrun_threshold]
                stats.overruns = len(overrun_deltas)
                stats.overrun_frac = stats.overruns / len(deltas)
                stats.worst_overrun_us = max(
                    (d - target_us for d in overrun_deltas), default=0
                )
            out[nid] = stats
        return out

    def aggregate(self) -> dict[str, float | int]:
        """Combined stats across nodes: worst-case for the sweep verdict."""
        per_node = self.report()
        if not per_node:
            return {"n_cycles": 0, "overrun_frac": 1.0, "mean_us": 0,
                    "worst_overrun_us": 0, "any_crc_errors": False,
                    "nodes_seen": 0}
        total_cycles = sum(s.n_cycles for s in per_node.values())
        total_overruns = sum(s.overruns for s in per_node.values())
        return {
            "n_cycles": total_cycles,
            "overrun_frac": total_overruns / max(1, total_cycles),
            "mean_us": sum(s.mean_us for s in per_node.values()) // len(per_node),
            "worst_overrun_us": max((s.worst_overrun_us for s in per_node.values()), default=0),
            "any_crc_errors": any(s.crc_errors for s in per_node.values()),
            "nodes_seen": len(per_node),
        }
