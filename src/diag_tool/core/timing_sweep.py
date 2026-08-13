"""
Timing sweep orchestrator.

Runs a descending sweep over candidate cycle durations. For each
candidate:
  1. Prepare configs (proportional in-cycle offsets).
  2. Bring nodes up (local subprocess spawn OR SSH restart).
  3. Wait for operational via diag get_status.
  4. Measure MEASURE_S seconds of STATE frames via CycleMeasurement.
  5. Tear nodes down.
  6. Compute verdict (STABLE / TOO_MANY_OVERRUNS / NO_OPERATIONAL /
     NODE_DIED / EXCEPTION).

Emits `on_result(candidate_result)` per candidate so the UI can update
live. `cancel()` interrupts the sweep at the next iteration boundary.
"""
from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from .config_gen import NodeSpec, make_spec, render_to_file, render_toml
from .diag_client import DiagClient
from .operational_listener import OperationalListener
from .ssh_deploy import HardwareNode, SshError, scp_bytes, scp_file, ssh_exec
from .timing_measure import CycleMeasurement


VERDICT_STABLE = "STABLE"
VERDICT_OVERRUNS = "TOO_MANY_OVERRUNS"
VERDICT_NO_OP = "NO_OPERATIONAL"
VERDICT_NODE_DIED = "NODE_DIED"
VERDICT_EXCEPTION = "EXCEPTION"


@dataclass
class SweepParams:
    candidates_ms: list[int]
    nominal: int
    minimum: int
    measure_s: float
    setup_timeout_s: float
    max_overrun_fraction: float
    diag_group: str
    diag_port: int
    op_group: str
    op_port: int
    interface_ip: str
    # simulated only
    local_binary: Optional[Path] = None
    local_work_dir: Optional[Path] = None
    # hardware only
    hardware_nodes: list[HardwareNode] = field(default_factory=list)


@dataclass
class CandidateResult:
    cycle_ms: int
    operational: bool
    n_cycles: int
    mean_us: int
    overrun_frac: float
    worst_overrun_us: int
    verdict: str
    detail: str = ""


class TimingSweep:
    def __init__(self, mode: str, params: SweepParams,
                 on_line: Callable[[str], None],
                 on_result: Callable[[CandidateResult], None],
                 on_done: Callable[[Optional[int]], None]) -> None:
        assert mode in ("simulated", "hardware")
        self.mode = mode
        self.p = params
        self.on_line = on_line
        self.on_result = on_result
        self.on_done = on_done
        self._cancel = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def cancel(self) -> None:
        self._cancel.set()

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="timing-sweep", daemon=True)
        self._thread.start()

    # ---- main loop -------------------------------------------------------

    def _run(self) -> None:
        smallest: Optional[int] = None
        try:
            for cycle_ms in self.p.candidates_ms:
                if self._cancel.is_set():
                    self.on_line("[cancelled]")
                    break
                self.on_line(f"\n=== candidate cycle_duration_ms = {cycle_ms} ===")
                try:
                    res = self._run_candidate(cycle_ms)
                except Exception as e:  # never let a candidate crash the sweep
                    res = CandidateResult(
                        cycle_ms=cycle_ms, operational=False,
                        n_cycles=0, mean_us=0, overrun_frac=1.0,
                        worst_overrun_us=0, verdict=VERDICT_EXCEPTION,
                        detail=f"{type(e).__name__}: {e}",
                    )
                    self.on_line(f"[exception] {res.detail}")
                self.on_result(res)
                if res.verdict == VERDICT_STABLE:
                    smallest = cycle_ms
        finally:
            self.on_done(smallest)

    def _run_candidate(self, cycle_ms: int) -> CandidateResult:
        # --- 1. bring nodes up + attach a measurement listener ----------
        expected_ids = self._expected_node_ids()
        listener = OperationalListener(
            multicast_group=self.p.op_group,
            port=self.p.op_port,
            interface_ip=self.p.interface_ip,
        )
        listener.start()
        try:
            meas = CycleMeasurement(target_cycle_ms=cycle_ms)
            meas.attach(listener)

            if self.mode == "simulated":
                self._sim_start(cycle_ms)
            else:
                self._hw_start(cycle_ms)

            # --- 2. wait for operational (via diag get_status) -----------
            diag = DiagClient(
                multicast_group=self.p.diag_group,
                port=self.p.diag_port,
                interface_ip=self.p.interface_ip,
            )
            diag.start()
            try:
                operational = self._wait_operational(
                    diag, expected_ids, self.p.setup_timeout_s
                )
            finally:
                diag.stop()

            if not operational:
                return CandidateResult(
                    cycle_ms=cycle_ms, operational=False,
                    n_cycles=0, mean_us=0, overrun_frac=1.0,
                    worst_overrun_us=0, verdict=VERDICT_NO_OP,
                    detail=f"only {len(expected_ids)} nodes expected, "
                           f"not all reached operational in "
                           f"{self.p.setup_timeout_s}s",
                )

            # --- 3. measure ----------------------------------------------
            self.on_line(f"measuring for {self.p.measure_s:.1f}s …")
            end = time.monotonic() + self.p.measure_s
            while time.monotonic() < end:
                if self._cancel.is_set():
                    break
                time.sleep(0.1)

            # --- 4. check liveness (simulated) ---------------------------
            died = False
            if self.mode == "simulated":
                died = any(p.poll() is not None for p in self._local_procs)

            # --- 5. verdict ----------------------------------------------
            agg = meas.aggregate()
            verdict = self._verdict(agg, died)
            detail = ""
            if verdict == VERDICT_NODE_DIED:
                detail = "one or more local node processes exited"
            elif verdict == VERDICT_OVERRUNS:
                detail = (f"{agg['overrun_frac']:.2%} > "
                          f"{self.p.max_overrun_fraction:.2%} (target)")

            return CandidateResult(
                cycle_ms=cycle_ms,
                operational=True,
                n_cycles=int(agg["n_cycles"]),
                mean_us=int(agg["mean_us"]),
                overrun_frac=float(agg["overrun_frac"]),
                worst_overrun_us=int(agg["worst_overrun_us"]),
                verdict=verdict,
                detail=detail,
            )
        finally:
            listener.stop()
            if self.mode == "simulated":
                self._sim_stop()
            else:
                self._hw_stop()

    def _verdict(self, agg: dict, died: bool) -> str:
        if died:
            return VERDICT_NODE_DIED
        if agg["overrun_frac"] > self.p.max_overrun_fraction:
            return VERDICT_OVERRUNS
        if agg["nodes_seen"] < self.p.nominal:
            return VERDICT_NO_OP
        return VERDICT_STABLE

    def _wait_operational(self, diag: DiagClient, ids: list[int],
                          timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self._cancel.is_set():
                return False
            ok = 0
            for nid in ids:
                status = diag.get_status(nid, timeout=0.5)
                if status is None:
                    continue
                state = status.get("node_state", "")
                if state in ("ReadInputs", "ShareInputs", "ShareResult",
                             "SendAck", "PublishResult", "CycleSync"):
                    ok += 1
            if ok >= len(ids):
                return True
            time.sleep(0.5)
        return False

    def _expected_node_ids(self) -> list[int]:
        if self.mode == "simulated":
            return list(range(self.p.nominal))
        return [n.node_id for n in self.p.hardware_nodes]

    # ---- simulated mode --------------------------------------------------

    _local_procs: list[subprocess.Popen] = []

    def _sim_start(self, cycle_ms: int) -> None:
        if self.p.local_binary is None or self.p.local_work_dir is None:
            raise RuntimeError("simulated mode requires local_binary and local_work_dir")
        cand_dir = self.p.local_work_dir / f"cycle_{cycle_ms}ms"
        cfg_dir = cand_dir / "configs"; cfg_dir.mkdir(parents=True, exist_ok=True)
        log_dir = cand_dir / "logs"; log_dir.mkdir(parents=True, exist_ok=True)

        self._local_procs = []
        for own_id in range(self.p.nominal):
            spec = make_spec(
                own_id=own_id,
                nominal=self.p.nominal,
                minimum=self.p.minimum,
                cycle_ms=cycle_ms,
                fabric_group=self.p.op_group,
                fabric_port=self.p.op_port,
                diag_group=self.p.diag_group,
                diag_port=self.p.diag_port,
                interface="lo",
            )
            cfg_path = render_to_file(spec, cfg_dir / f"node_{own_id}.toml")
            log_path = log_dir / f"node_{own_id}.log"
            log_fh = log_path.open("w")
            p = subprocess.Popen(
                [str(self.p.local_binary), "--config", str(cfg_path)],
                stdout=log_fh, stderr=subprocess.STDOUT,
            )
            self._local_procs.append(p)
        self.on_line(f"spawned {len(self._local_procs)} local nodes")

    def _sim_stop(self) -> None:
        for p in self._local_procs:
            try:
                if p.poll() is None:
                    p.terminate()
            except OSError:
                pass
        for p in self._local_procs:
            try:
                p.wait(timeout=3.0)
            except subprocess.TimeoutExpired:
                try:
                    p.kill()
                except OSError:
                    pass
        self._local_procs = []

    # ---- hardware mode ---------------------------------------------------

    def _hw_start(self, cycle_ms: int) -> None:
        if not self.p.hardware_nodes:
            raise RuntimeError("hardware mode requires at least one HardwareNode")
        # For each configured hardware node, render its TOML with the
        # current cycle_ms, SCP it into place, then run the start command.
        for hn in self.p.hardware_nodes:
            spec = make_spec(
                own_id=hn.node_id,
                nominal=self.p.nominal,
                minimum=self.p.minimum,
                cycle_ms=cycle_ms,
                fabric_group=self.p.op_group,
                fabric_port=self.p.op_port,
                diag_group=self.p.diag_group,
                diag_port=self.p.diag_port,
                interface="lo",  # node's own view; hardware uses eth by config
            )
            toml_text = render_toml(spec)
            self.on_line(f"[{hn.host}] deploying config → {hn.remote_config}")
            scp_bytes(hn, toml_text.encode(), hn.remote_config)
            # stop first (best-effort, ignore failures), then start
            try:
                ssh_exec(hn, hn.resolved_stop_cmd(), timeout=10.0)
            except SshError as e:
                self.on_line(f"[{hn.host}] stop_cmd failed (continuing): {e.output}")
            self.on_line(f"[{hn.host}] starting")
            try:
                ssh_exec(hn, hn.resolved_start_cmd(), timeout=15.0)
            except SshError as e:
                raise RuntimeError(
                    f"[{hn.host}] start_cmd failed: {e.output}"
                ) from e

    def _hw_stop(self) -> None:
        for hn in self.p.hardware_nodes:
            try:
                ssh_exec(hn, hn.resolved_stop_cmd(), timeout=10.0)
            except SshError as e:
                self.on_line(f"[{hn.host}] stop failed (ignored): {e.output}")
