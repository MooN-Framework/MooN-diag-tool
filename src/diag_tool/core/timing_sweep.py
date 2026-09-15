"""
Timing sweep orchestrator.

Runs a descending sweep over candidate cycle durations, looking for the
smallest cycle_ms the system still runs stably at. For each candidate:
  1. Prepare configs (proportional in-cycle offsets).
  2. Bring nodes up (local subprocess spawn OR SSH restart).
  3. Wait for operational via diag get_status.
  4. Wait `stability_wait_s` seconds, doing nothing.
  5. Check: is every expected node STILL reporting a healthy state
     (i.e. not Startup/InitSync/Isolation/Failsafe/ErrorManagement) via
     diag get_status? That's the whole check -- "is the full system
     still up and running after waiting", nothing more elaborate.
  6. Tear nodes down.
  7. Compute verdict (STABLE / NOT_STABLE / NO_OPERATIONAL /
     NODE_DIED / EXCEPTION).

Emits `on_result(candidate_result)` per candidate so the UI can update
live. `cancel()` interrupts the sweep at the next iteration boundary.
"""
from __future__ import annotations

import os
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from .diag_client import DiagClient
from .ssh_deploy import (
    HardwareNode,
    SshError,
    ensure_remote_dirs,
    is_process_running,
    scp_bytes,
    ssh_exec,
    stop_moon_node_service,
    tail_remote_logs,
)

# The test harness lives alongside the diag tool in the same repo now,
# so we can reuse its config generation directly.
from harness.config_gen import make_spec, render_config, render_toml_str


def render_config_to_str(spec) -> str:
    """Backwards-compatible alias for the SCP-to-hardware path."""
    return render_toml_str(spec)


class SweepAbort(RuntimeError):
    """A failure that will hit every candidate identically.

    A wrong argv, a binary the OS refuses to exec, a config the node
    rejects outright: retrying the next cycle_ms cannot change any of
    these, it only produces the same error N more times and buries the
    first (and only useful) message. The candidate loop treats this as
    "stop the sweep" rather than "this candidate failed".
    """


VERDICT_STABLE = "STABLE"
VERDICT_NOT_STABLE = "NOT_STABLE"
VERDICT_NO_OP = "NO_OPERATIONAL"
VERDICT_NODE_DIED = "NODE_DIED"
VERDICT_EXCEPTION = "EXCEPTION"

# States that mean "this node is up but not in normal service" -- a node
# reporting one of these after the stability wait means the candidate
# cycle_ms did NOT run stably, full stop. Includes the two "still
# booting" states too, reused as the not-yet-operational check.
NOT_YET_UP_STATES = {"Startup", "InitSync"}
UNHEALTHY_STATES = NOT_YET_UP_STATES | {"Isolation", "Failsafe", "ErrorManagement"}


@dataclass
class SweepParams:
    candidates_ms: list[int]
    nominal: int
    minimum: int
    stability_wait_s: float  # how long to wait, doing nothing, before re-checking every node is still healthy
    setup_timeout_s: float
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
    node_interface: str = "eth0"  # interface NAME baked into the node's config.toml


@dataclass
class CandidateResult:
    cycle_ms: int
    operational: bool
    still_healthy_after_wait: bool
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
        # Set as soon as any node ever replies on the diag channel. Used
        # to tell "the system is too slow at this cycle_ms" apart from
        # "there is no diag channel to talk to at all" -- see the hint
        # emitted on VERDICT_NO_OP.
        self._any_node_answered = False
        # Instance-level, not a class attribute: a mutable default on the
        # class is shared by every TimingSweep ever constructed, so a
        # second sweep in the same GUI session would inherit the first
        # one's (already terminated) Popen objects if _sim_stop ran
        # before _sim_start.
        self._local_procs: list[subprocess.Popen] = []

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
                except SweepAbort as e:
                    self.on_result(CandidateResult(
                        cycle_ms=cycle_ms, operational=False,
                        still_healthy_after_wait=False, verdict=VERDICT_EXCEPTION,
                        detail=str(e),
                    ))
                    self.on_line(f"[abort] {e}")
                    self.on_line(
                        "[abort] stopping the sweep: this failure is "
                        "independent of the cycle duration, so the remaining "
                        "candidates would fail identically"
                    )
                    break
                except Exception as e:  # never let a candidate crash the sweep
                    res = CandidateResult(
                        cycle_ms=cycle_ms, operational=False,
                        still_healthy_after_wait=False, verdict=VERDICT_EXCEPTION,
                        detail=f"{type(e).__name__}: {e}",
                    )
                    self.on_line(f"[exception] {res.detail}")
                self.on_result(res)
                if res.verdict == VERDICT_STABLE:
                    smallest = cycle_ms
        finally:
            self.on_done(smallest)

    def _run_candidate(self, cycle_ms: int) -> CandidateResult:
        # --- 1. bring nodes up -----------------------------------------
        expected_ids = self._expected_node_ids()
        if self.mode == "simulated":
            self._sim_start(cycle_ms)
        else:
            self._hw_start(cycle_ms)
        try:
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

                if not operational:
                    self._log_node_tails(cycle_ms)
                    if not self._any_node_answered:
                        # Not one node ever replied on the diag channel.
                        # That is almost never a timing problem: a node
                        # built without the "diagnostic" feature comes up
                        # and runs perfectly well, it just has no diag
                        # channel to answer on. Name that explicitly --
                        # the bare timeout message sent people looking at
                        # cycle durations for a build-flag problem.
                        self.on_line(
                            "[hint] no node answered on the diag channel at all "
                            f"({self.p.diag_group}:{self.p.diag_port} via "
                            f"{self.p.interface_ip}). Usual cause: the binary "
                            "was built WITHOUT the 'diagnostic' feature, so the "
                            "channel does not exist. Check Settings > Build > "
                            "Features, then tick 'Force rebuild' and start the "
                            "sweep again. Other causes: wrong interface IP, or "
                            "multicast blocked on that interface."
                        )
                    return CandidateResult(
                        cycle_ms=cycle_ms, operational=False,
                        still_healthy_after_wait=False, verdict=VERDICT_NO_OP,
                        detail=(
                            "no node answered on the diag channel -- see the "
                            "hint in the log, this is usually a missing "
                            "'diagnostic' build feature rather than a timing "
                            "limit"
                            if not self._any_node_answered else
                            f"only {len(expected_ids)} nodes expected, "
                            f"not all reached operational in "
                            f"{self.p.setup_timeout_s}s "
                            f"(see log for node stdout tails)"
                        ),
                    )

                # --- 3. wait, doing nothing -------------------------------
                self.on_line(f"waiting {self.p.stability_wait_s:.1f}s to check stability …")
                end = time.monotonic() + self.p.stability_wait_s
                while time.monotonic() < end:
                    if self._cancel.is_set():
                        break
                    time.sleep(0.1)

                # --- 4. check liveness (simulated) ------------------------
                died = False
                if self.mode == "simulated":
                    died = any(p.poll() is not None for p in self._local_procs)

                # --- 5. is the WHOLE system still healthy? ----------------
                # A single diag get_status round per node, right now --
                # this is deliberately just "ask each node what state
                # it's in and check none of them fell over", not a
                # statistics-gathering pass. See _probe_states().
                states = self._probe_states(diag, expected_ids)
                unhealthy = {
                    nid: st for nid, st in states.items()
                    if st is None or st.get("node_state") in UNHEALTHY_STATES
                }
                still_healthy = not unhealthy and not died
            finally:
                diag.stop()

            # --- 6. verdict --------------------------------------------------
            if died:
                verdict, detail = VERDICT_NODE_DIED, "one or more local node processes exited"
            elif unhealthy:
                verdict = VERDICT_NOT_STABLE
                parts = []
                for nid, st in sorted(unhealthy.items()):
                    state = st.get("node_state") if st else None
                    parts.append(f"node {nid}: {state if state else 'no response'}")
                detail = "; ".join(parts)
            else:
                verdict, detail = VERDICT_STABLE, ""

            return CandidateResult(
                cycle_ms=cycle_ms,
                operational=True,
                still_healthy_after_wait=still_healthy,
                verdict=verdict,
                detail=detail,
            )
        finally:
            if self.mode == "simulated":
                self._sim_stop()
            else:
                self._hw_stop()

    def _log_node_tails(self, cycle_ms: int) -> None:
        """Surface WHY nodes didn't come up (or didn't stay healthy) by
        tailing their logs. Simulated: local stdout log files. Hardware:
        the node binary's own --log-dir current-session log (plus the
        raw stdout/stderr catch-all) fetched over SSH."""
        if self.mode == "simulated" and self.p.local_work_dir is not None:
            log_dir = self.p.local_work_dir / f"cycle_{cycle_ms}ms" / "logs"
            for lp in sorted(log_dir.glob("node_*.log")):
                try:
                    tail = lp.read_text(errors="replace").splitlines()[-8:]
                except OSError:
                    continue
                self.on_line(f"--- {lp.name} (tail) ---")
                for ln in tail:
                    self.on_line(ln)
        elif self.mode == "hardware":
            for hn in sorted(self.p.hardware_nodes, key=lambda n: n.node_id):
                self.on_line(f"=== node {hn.node_id} ({hn.host}) ===")
                for ln in tail_remote_logs(hn, lines=8).splitlines():
                    self.on_line(ln)

    def _probe_states(self, diag: DiagClient, ids: list[int]) -> dict[int, Optional[dict]]:
        """One diag get_status round per node, in parallel. None for a
        node that didn't answer in time (treated as unhealthy by the
        caller -- a node that can't even be asked isn't "stable")."""
        results: dict[int, Optional[dict]] = {}
        lock = threading.Lock()

        def probe(nid: int) -> None:
            st = diag.get_status(nid, timeout=1.5)
            with lock:
                results[nid] = st

        threads = [threading.Thread(target=probe, args=(nid,), daemon=True) for nid in ids]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=2.0)
        for nid in ids:
            results.setdefault(nid, None)
        return results

    def _wait_operational(self, diag: DiagClient, ids: list[int],
                          timeout: float) -> bool:
        """
        Poll get_status on all expected nodes. A node counts as operational
        as soon as its diag channel answers with any state other than
        Startup/InitSync (i.e. it has finished coming up). We probe all
        ids in parallel each round so a slow node doesn't serialise the
        others.
        """
        deadline = time.monotonic() + timeout
        ready: set[int] = set()
        while time.monotonic() < deadline:
            if self._cancel.is_set():
                return False
            missing = [nid for nid in ids if nid not in ready]
            if not missing:
                return True

            results: dict[int, Optional[dict]] = {}
            lock = threading.Lock()

            def probe(nid: int) -> None:
                st = diag.get_status(nid, timeout=0.8)
                with lock:
                    results[nid] = st

            threads = [threading.Thread(target=probe, args=(nid,), daemon=True)
                       for nid in missing]
            for t in threads:
                t.start()
            for t in threads:
                t.join(timeout=1.5)

            for nid, st in results.items():
                if st is None:
                    continue
                self._any_node_answered = True
                state = st.get("node_state", "")
                if state and state not in NOT_YET_UP_STATES:
                    ready.add(nid)

            self.on_line(
                f"[wait_operational] ready={sorted(ready)} "
                f"missing={sorted(set(ids) - ready)}"
            )
            if len(ready) >= len(ids):
                return True
            time.sleep(0.3)
        return False

    def _expected_node_ids(self) -> list[int]:
        if self.mode == "simulated":
            return list(range(self.p.nominal))
        return [n.node_id for n in self.p.hardware_nodes]

    # ---- simulated mode --------------------------------------------------

    def _sim_start(self, cycle_ms: int) -> None:
        if self.p.local_binary is None or self.p.local_work_dir is None:
            raise RuntimeError("simulated mode requires local_binary and local_work_dir")
        cand_dir = self.p.local_work_dir / f"cycle_{cycle_ms}ms"
        cfg_dir = cand_dir / "configs"; cfg_dir.mkdir(parents=True, exist_ok=True)
        log_dir = cand_dir / "logs"; log_dir.mkdir(parents=True, exist_ok=True)
        # The node writes its own session log via --log-dir. Keep that
        # out of `logs/`, whose node_*.log files are the stdout capture
        # that _log_node_tails globs -- otherwise every tail would show
        # up twice, once from stdout and once from the session file.
        session_dir = cand_dir / "sessions"; session_dir.mkdir(parents=True, exist_ok=True)

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
                interface="lo",  # simulated nodes are local subprocesses on loopback -- always "lo"
                init_sync_timeout_ms=15_000,
            )
            cfg_path = render_config(spec, cfg_dir / f"node_{own_id}.toml")
            log_path = log_dir / f"node_{own_id}.log"
            log_fh = log_path.open("w")
            # Exactly the argv the harness uses (harness/node.py): the
            # binary takes --config directly, there is no subcommand.
            # This used to pass a leading "node" argument, which every
            # node rejected with "unknown argument: node" and an exit
            # before it had opened a single socket. Nothing looked at
            # the processes afterwards, so the sweep spent the full
            # setup timeout polling a diag channel that belonged to
            # three already-dead processes. Keep this in step with
            # harness/node.py if the node CLI ever changes.
            # RUST_LOG/NO_COLOR match the harness defaults.
            env = os.environ.copy()
            env.setdefault("RUST_LOG", "info")
            env["NO_COLOR"] = "1"
            cmd = [
                str(self.p.local_binary),
                "--config", str(cfg_path),
                "--log-dir", str(session_dir),
            ]
            p = subprocess.Popen(
                cmd, env=env,
                stdout=log_fh, stderr=subprocess.STDOUT,
            )
            self._local_procs.append(p)
        self.on_line(
            f"spawned {len(self._local_procs)} local nodes "
            f"(cfg={cfg_dir}, logs={log_dir})"
        )

        # Fail fast on a node that never got off the ground. A bad argv,
        # a config the node rejects or a missing shared library kills the
        # process in milliseconds, and without this check the sweep went
        # on to wait out the whole setup timeout per candidate before
        # showing the reason. Half a second is far more than the node
        # needs to reject its arguments and far less than it needs to
        # become operational, so a process still alive here failed for
        # some other reason and is left to the normal timeout path.
        time.sleep(0.5)
        stillborn = [
            own_id for own_id, proc in enumerate(self._local_procs)
            if proc.poll() is not None
        ]
        if stillborn:
            codes = ", ".join(
                f"node {own_id} exit={self._local_procs[own_id].returncode}"
                for own_id in stillborn
            )
            self.on_line(
                f"[error] {len(stillborn)} of {len(self._local_procs)} node "
                f"processes exited immediately after spawn ({codes}). "
                f"The argv was: {' '.join(cmd)}"
            )
            self._log_node_tails(cycle_ms)
            raise SweepAbort(
                f"local node processes did not start: {codes}. "
                "See the tail above for the reason the binary gave."
            )

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
        # For each configured hardware node, stop moon-node.service first
        # (best-effort -- see stop_moon_node_service docstring: without
        # this, a node that was ever deployed to via the Package tab
        # keeps running the production binary from /opt/moon/bin/node
        # alongside the harness's own test binary, both fighting over
        # the same multicast group/port), then render its TOML with the
        # current cycle_ms, SCP it into place, and run the start command.
        for hn in self.p.hardware_nodes:
            self.on_line(f"[{hn.host}] stopping moon-node.service (if present)")
            stop_moon_node_service(hn)
            warn = hn.validate_logging()
            if warn:
                self.on_line(f"WARNING: {warn}")
            try:
                ensure_remote_dirs(hn)
            except SshError as e:
                raise RuntimeError(f"[{hn.host}] mkdir failed: {e.output}") from e
            spec = make_spec(
                own_id=hn.node_id,
                nominal=self.p.nominal,
                minimum=self.p.minimum,
                cycle_ms=cycle_ms,
                fabric_group=self.p.op_group,
                fabric_port=self.p.op_port,
                diag_group=self.p.diag_group,
                diag_port=self.p.diag_port,
                interface=self.p.node_interface,  # e.g. "eth0" -- the node's own interface, not this machine's
                init_sync_timeout_ms=15_000,  # matches the new NodeSpec/make_spec default, kept explicit for clarity here
            )
            toml_text = render_config_to_str(spec)
            self.on_line(f"[{hn.host}] deploying config → {hn.remote_config}")
            scp_bytes(hn, toml_text.encode(), hn.remote_config)
            # stop first (best-effort, ignore failures), then start
            try:
                ssh_exec(hn, hn.resolved_stop_cmd(), timeout=30.0)
            except SshError as e:
                self.on_line(f"[{hn.host}] stop_cmd failed (continuing): {e.output}")
            time.sleep(0.3)  # let the old process actually exit before the new one rebinds its sockets
            self.on_line(f"[{hn.host}] starting")
            try:
                ssh_exec(hn, hn.resolved_start_cmd(), timeout=30.0)
            except SshError as e:
                raise RuntimeError(
                    f"[{hn.host}] start_cmd failed: {e.output}"
                ) from e
            # start_cmd exiting 0 only proves the shell backgrounded
            # something -- not that it's still alive. Verify for real.
            time.sleep(0.5)
            if not is_process_running(hn):
                tail = tail_remote_logs(hn)
                raise RuntimeError(
                    f"[{hn.host}] process not running after start_cmd "
                    f"(nohup/& exits 0 even on an immediate crash) -- "
                    f"log tail:\n{tail}"
                )

    def _hw_stop(self) -> None:
        for hn in self.p.hardware_nodes:
            try:
                ssh_exec(hn, hn.resolved_stop_cmd(), timeout=30.0)
            except SshError as e:
                self.on_line(f"[{hn.host}] stop failed (ignored): {e.output}")
