"""
Passive cycle-timing measurement over the operational multicast.

Every operational frame carries a header field `seq_num` that is
constant within one cycle and increments once per cycle. That's the
only reliable cycle heartbeat available to a passive observer -- STATE
payloads for example are emitted several times per cycle during the
CycleSync phase and not at all during others, so gating on payload
type would badly skew the deltas.

We therefore:

  1. Watch every valid (CRC-ok) frame per node.
  2. Note the monotonic timestamp of the FIRST frame we see for each
     new `seq_num`. That timestamp marks the observed start of that
     cycle.
  3. Delta between two consecutive cycle-start timestamps = the cycle's
     duration.
  4. Only record deltas where seq advanced by exactly 1 -- if we missed
     frames (e.g. loopback drop, restart mid-measurement) we re-baseline
     without inventing a data point.
  5. Session-id change (node restart) re-baselines cleanly.

The overrun threshold is `target + max(500 us, target * pct/100)`.
That floor covers sub-ms cycles where 5 % would be below measurement
noise; for a 20 ms target the 5 % rule gives 1 ms tolerance, for 100 ms
it gives 5 ms.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .operational_listener import OperationalListener
from .wire_decoder import DecodedFrame


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
    missed_cycles: int = 0     # seq gaps > 1
    session_resets: int = 0    # session_id changed mid-measurement


@dataclass
class CycleMeasurement:
    target_cycle_ms: int
    overrun_tolerance_pct: float = 5.0

    # Per node: (session_id, last_seq, ts_of_first_frame_this_cycle)
    _cycle_start: dict[int, tuple[int, int, float]] = field(default_factory=dict)
    _deltas_us: dict[int, list[int]] = field(default_factory=dict)
    _crc_errors: dict[int, int] = field(default_factory=dict)
    _frames_total: dict[int, int] = field(default_factory=dict)
    _missed_cycles: dict[int, int] = field(default_factory=dict)
    _session_resets: dict[int, int] = field(default_factory=dict)

    @property
    def overrun_tolerance_us(self) -> int:
        target_us = self.target_cycle_ms * 1000
        return max(500, int(target_us * self.overrun_tolerance_pct / 100.0))

    def attach(self, listener: OperationalListener) -> None:
        listener.add_listener(self._on_frame)

    def _on_frame(self, frame: DecodedFrame, ts_mono: float) -> None:
        nid = frame.node_id
        self._frames_total[nid] = self._frames_total.get(nid, 0) + 1
        if not frame.crc_ok:
            self._crc_errors[nid] = self._crc_errors.get(nid, 0) + 1
            return

        prev = self._cycle_start.get(nid)
        if prev is None:
            # First frame ever from this node — baseline.
            self._cycle_start[nid] = (frame.session_id, frame.seq_num, ts_mono)
            return

        prev_session, prev_seq, prev_ts = prev

        if frame.session_id != prev_session:
            # Node restart. Drop the point but count it.
            self._session_resets[nid] = self._session_resets.get(nid, 0) + 1
            self._cycle_start[nid] = (frame.session_id, frame.seq_num, ts_mono)
            return

        if frame.seq_num == prev_seq:
            # Still within the same cycle — every phase sends several
            # frames, we only care about the FIRST one per seq.
            return

        seq_advance = frame.seq_num - prev_seq
        if seq_advance < 0:
            # seq_num decreased without a session change — treat as anomaly
            # and re-baseline without recording.
            self._cycle_start[nid] = (frame.session_id, frame.seq_num, ts_mono)
            return

        delta_us = int((ts_mono - prev_ts) * 1_000_000)

        if seq_advance == 1:
            # Clean single-cycle advance: this is the measurement we want.
            self._deltas_us.setdefault(nid, []).append(delta_us)
        else:
            # We missed (seq_advance - 1) cycles worth of frames. Do not
            # record a distorted delta; count the gap for diagnostics.
            self._missed_cycles[nid] = (
                self._missed_cycles.get(nid, 0) + (seq_advance - 1)
            )

        self._cycle_start[nid] = (frame.session_id, frame.seq_num, ts_mono)

    def report(self) -> dict[int, NodeTimingStats]:
        target_us = self.target_cycle_ms * 1000
        overrun_threshold = target_us + self.overrun_tolerance_us
        out: dict[int, NodeTimingStats] = {}
        all_ids = set(self._frames_total.keys()) | set(self._deltas_us.keys())
        for nid in sorted(all_ids):
            deltas = self._deltas_us.get(nid, [])
            stats = NodeTimingStats(node_id=nid)
            stats.frames_total = self._frames_total.get(nid, 0)
            stats.crc_errors = self._crc_errors.get(nid, 0)
            stats.missed_cycles = self._missed_cycles.get(nid, 0)
            stats.session_resets = self._session_resets.get(nid, 0)
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

    def aggregate(self) -> dict[str, float | int | bool]:
        """Worst-case stats across nodes, used to decide sweep verdicts."""
        per_node = self.report()
        if not per_node:
            return {
                "n_cycles": 0, "overrun_frac": 1.0, "mean_us": 0,
                "worst_overrun_us": 0, "any_crc_errors": False,
                "nodes_seen": 0, "missed_cycles": 0,
            }
        total_cycles = sum(s.n_cycles for s in per_node.values())
        total_overruns = sum(s.overruns for s in per_node.values())
        return {
            "n_cycles": total_cycles,
            "overrun_frac": total_overruns / max(1, total_cycles),
            "mean_us": (
                sum(s.mean_us for s in per_node.values() if s.n_cycles)
                // max(1, sum(1 for s in per_node.values() if s.n_cycles))
            ),
            "worst_overrun_us": max(
                (s.worst_overrun_us for s in per_node.values()), default=0
            ),
            "any_crc_errors": any(s.crc_errors for s in per_node.values()),
            "nodes_seen": len(per_node),
            "missed_cycles": sum(s.missed_cycles for s in per_node.values()),
        }
