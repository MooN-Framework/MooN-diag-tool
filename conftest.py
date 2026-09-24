"""
Pytest fixtures for the MooN test framework.

Lives at the repo root, loaded automatically by pytest.

CLI options:
  --rust-repo=PATH      Location of the Rust node repo (Cargo.toml). Default:
                        env var RUST_REPO, or ../voting-node relative to this repo.
  --fabric=MODE         "simulated" (default) builds locally via cargo and
                        spawns processes; "hardware" drives real nodes over
                        the configured multicast groups.
  --hw-nodes-file=PATH  Only for --fabric=hardware: path to a JSON file with
                        the node connection list (schema:
                        diag_tool.core.ssh_deploy.HardwareNode -- host, user,
                        port, password, remote_binary/config/log/log_dir,
                        start_cmd/stop_cmd). The diag tool's Test tab writes
                        this file automatically (0600, temp) before every
                        hardware run and cleans it up afterwards. Without
                        this file, neither restart_node() nor
                        Node.wait_for_log() work in hardware mode --
                        see harness/hw_node.py.
  --diag-group / --diag-port / --op-group / --op-port / --interface-ip
                        Multicast configuration for hardware mode.

Key fixtures:
- `binary` (session-scope): path to the compiled Rust node binary.
  Built with `cargo build --features diagnostic` on first use, then cached.
- `work_dir` (function-scope): a temp directory per test.
- `fabric_3` / `fabric_4` (function-scope): fabric fixtures. In hardware
  mode these are backed by a `--hw-nodes-file`-based adapter
  (see _HardwareFabric) with a real restart_node() and log-based
  wait_for_log() assertions over the framework's --log-dir file logging.
"""
from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from typing import Optional

import pytest

from harness.fabric import Fabric, FabricOptions


REPO_ROOT = Path(__file__).resolve().parent


# ---- CLI options -----------------------------------------------------

def pytest_addoption(parser):
    parser.addoption(
        "--rust-repo", default=None,
        help="Path to the Rust node repo (must contain Cargo.toml). "
             "Falls back to $RUST_REPO or ../voting-node relative to this repo.",
    )
    parser.addoption(
        "--fabric", default="simulated",
        choices=["simulated", "hardware"],
        help="Fabric backend: simulated (local cargo + subprocess) or "
             "hardware (SSH-restart of already-installed nodes).",
    )
    parser.addoption(
        "--hw-nodes-file", default=None,
        help="Path to a JSON file with the hardware node connection list "
             "(same schema as diag_tool.core.ssh_deploy.HardwareNode). "
             "Required for --fabric=hardware -- the diag tool's Test tab "
             "writes this automatically before each hardware run.",
    )
    parser.addoption("--diag-group",    default="239.10.0.2")
    parser.addoption("--diag-port",     default=6666, type=int)
    parser.addoption("--op-group",      default="239.10.0.1")
    parser.addoption("--op-port",       default=5555, type=int)
    parser.addoption("--interface-ip",  default="127.0.0.1")
    parser.addoption("--discovery-timeout", default=10.0, type=float)


def _resolve_rust_repo(config) -> Path:
    override = config.getoption("--rust-repo")
    if override:
        return Path(override).resolve()
    env = os.environ.get("RUST_REPO")
    if env:
        return Path(env).resolve()
    return (REPO_ROOT.parent / "voting-node").resolve()


# ---- binary fixture --------------------------------------------------

@pytest.fixture(scope="session")
def binary(request) -> Path | None:
    """
    In simulated mode: cargo build --bin node --features diagnostic in
    the Rust repo, returns the resulting binary path.

    In hardware mode: returns None — cargo build would be pointless
    (nodes are already installed).
    """
    if request.config.getoption("--fabric") == "hardware":
        return None
    rust_repo = _resolve_rust_repo(request.config)
    if not (rust_repo / "Cargo.toml").exists():
        pytest.fail(
            f"Rust repo not found or has no Cargo.toml: {rust_repo}\n"
            f"Pass --rust-repo=PATH or set $RUST_REPO."
        )
    print(f"\n[fixture] cargo build ({rust_repo}) ...")
    t0 = time.monotonic()
    subprocess.run(
        ["cargo", "build", "--bin", "node", "--features", "diagnostic"],
        cwd=rust_repo, check=True,
    )
    print(f"[fixture] cargo build done in {time.monotonic() - t0:.1f}s")
    return rust_repo / "target" / "debug" / "node"


@pytest.fixture
def work_dir(tmp_path: Path) -> Path:
    return tmp_path


# ---- simulated fabric ------------------------------------------------

def _make_sim_fabric(binary: Path, work_dir: Path, nominal: int, minimum: int) -> Fabric:
    opts = FabricOptions(
        nominal=nominal, minimum=minimum,
        binary=binary, work_dir=work_dir,
        cycle_duration_ms=20,
    )
    f = Fabric(opts=opts)
    f.start_all()
    if not f.wait_operational(timeout=15.0):
        # Show WHY -- without this, a hang (never reaching CycleSyncOk)
        # and an immediate crash both look identical: a bare
        # "RuntimeError: fabric did not reach operational state" with
        # zero diagnostic content. Tail each local node's own captured
        # stdout (crash/panic output, or nothing at all if it's stuck
        # silently waiting on peer discovery -- which itself points at
        # a multicast/network problem on loopback, not a Rust bug).
        print(f"[sim-fabric] FAILED to reach operational -- tailing {len(f.nodes)} node log(s):", flush=True)
        for nid, node in sorted(f.nodes.items()):
            print(f"--- node {nid} (pid alive={node.is_running()}) ---", flush=True)
            tail = node.log_lines[-15:]
            if not tail:
                print("  (no output captured at all)", flush=True)
            for ln in tail:
                print(f"  {ln}", flush=True)
        f.stop_all()
        raise RuntimeError("fabric did not reach operational state")
    return f


# ---- hardware fabric adapter ----------------------------------------

class _HardwareFabric:
    """
    Duck-typed drop-in for harness.fabric.Fabric in hardware mode.
    Spawns nothing -- nodes are already-running, already-installed
    hardware. `nodes` holds one harness.hw_node.RemoteNode per
    configured+enabled hardware node from --hw-nodes-file. Once
    restart_all() has run, each one tails its own --log-dir
    current-session log over SSH (see RemoteNode.start_tailing), so
    Node.wait_for_log() works the same as it does against a local
    subprocess.
    """
    def __init__(self, diag, nodes: dict[int, "RemoteNode"]):
        self.diag = diag
        self.nodes = nodes

    def alive_ids(self):
        return [nid for nid, n in self.nodes.items() if n.is_running()]

    def stop_all(self):
        # Only tears down the local SSH tail helpers + diag socket --
        # deliberately does NOT stop the remote node processes at
        # TEARDOWN. The NEXT test's fixture setup calls restart_all()
        # anyway, which stops every node (verified) before starting any
        # of them, so stopping here too would just be redundant work.
        try:
            self.diag.close()
        except Exception:
            pass
        for node in self.nodes.values():
            node._stop_tailing()

    # ---- remote process control ---------------------------------------

    @staticmethod
    def _stop(rn) -> None:
        """Stop the remote process and wait until it is really gone
        (SIGKILL fallback). Raises SshError if it survives."""
        from diag_tool.core.ssh_deploy import stop_and_wait
        print(f"[restart] node {rn.node_id} ({rn.hw.host}): stopping", flush=True)
        out = stop_and_wait(rn.hw)
        if out:
            print(f"[restart] node {rn.node_id} ({rn.hw.host}): {out}", flush=True)

    @staticmethod
    def _start(rn, old_session: str, session_timeout: float = 10.0) -> None:
        """Run start_cmd and wait until the node has actually opened a
        NEW log session (current-log symlink repointed). start_cmd's
        `nohup ... & disown` returns 0 even if the binary crashes
        immediately, so this is the first real proof of life."""
        from diag_tool.core.ssh_deploy import (
            ssh_exec, tail_remote_logs, wait_log_session_change,
        )
        print(f"[restart] node {rn.node_id} ({rn.hw.host}): starting", flush=True)
        start_out = ssh_exec(rn.hw, rn.hw.resolved_start_cmd(), timeout=15.0)
        if start_out.strip():
            # A failed redirect in the backgrounded job is only visible
            # here (rc stays 0), see ssh_deploy.ensure_remote_dirs().
            print(f"[restart] node {rn.node_id} ({rn.hw.host}): start_cmd output: "
                  f"{start_out.strip()}", flush=True)
        if not wait_log_session_change(rn.hw, old_session, timeout=session_timeout):
            raise RuntimeError(
                f"node {rn.node_id} ({rn.hw.host}): no new log session within "
                f"{session_timeout:.0f}s after start_cmd\n{tail_remote_logs(rn.hw)}"
            )

    def restart_node(self, node_id: int, wait_operational: float = 8.0) -> None:
        """
        Stop + start the real remote node process via SSH. Returns once
        the old process is verifiably gone and the new one has opened
        its log session. The node's tail subprocess is left running
        throughout -- `tail -F` reopens the current-log symlink by path
        once the new session's file appears, so the same RemoteNode
        keeps streaming log lines across the restart (see
        harness/hw_node.py docstring). No wait_operational check, same
        as the simulated Fabric: the rejoin flow runs over
        ResyncLostPeer, not CycleSyncOk, so the test itself should wait
        via wait_peer_health(..., "Probation").
        """
        if node_id not in self.nodes:
            raise KeyError(f"unknown node_id {node_id}")
        from diag_tool.core.ssh_deploy import log_session_id

        rn = self.nodes[node_id]
        old_session = log_session_id(rn.hw)
        self._stop(rn)
        self._start(rn, old_session)
        _ = wait_operational  # reserved, mirrors the simulated Fabric

    def wait_operational(self, timeout: float = 15.0, after: Optional[dict[int, int]] = None) -> bool:
        """Same detection pattern as the simulated Fabric.wait_operational:
        each node's current-session log (tailed live over SSH -- see
        RemoteNode.start_tailing/wait_for_log) prints this line once
        discovery + sync finished and the first cycle is running.

        `after`: optional {node_id: line_count} watermark, see
        RemoteNode.line_count. Not needed after restart_all(), whose
        tails only ever see the fresh session.
        """
        pat = r"transition from=CycleSync event=CycleSyncOk to=ReadInputs"
        for node_id, node in self.nodes.items():
            watermark = after.get(node_id, 0) if after else 0
            print(f"[wait] node {node_id}: waiting for operational (timeout={timeout:.0f}s)...", flush=True)
            if not node.wait_for_log(pat, timeout=timeout, after=watermark):
                print(f"[wait] node {node_id}: TIMEOUT -- did not reach operational", flush=True)
                return False
            print(f"[wait] node {node_id}: operational", flush=True)
        return True

    def restart_all(self, wait_operational: float = 15.0) -> bool:
        """Give every managed node a fresh boot, equivalent to the
        simulated Fabric's start_all() + wait_operational().

        Three phases, each run on all nodes in parallel:
          1. stop every node and wait until each process is really
             gone,
          2. start every node and wait until each has opened a new log
             session,
          3. only then start the SSH log tails and wait for operational.

        Why this shape (each point was a source of sporadic failures):
        - A rolling restart (stop+start node 0, then node 1, ...) booted
          node 0 into a fabric whose other members were still the
          previous test's processes, possibly faulted, and then killed
          them underneath it. What node 0 made of that depended on SSH
          latency.
        - A fixed sleep after stop_cmd did not guarantee the old process
          had exited, so two processes with the same node_id could be
          live at once.
        - Tails were started before the restart with `tail -n +1`, so
          each RemoteNode's buffer began with the previous test's whole
          session. The line_count watermark meant to skip it was taken
          before the SSH tail had even connected (i.e. usually 0), so
          the old session's CycleSyncOk satisfied the readiness check
          and every log assertion in the test could match lines from
          the previous test.
        """
        from concurrent.futures import ThreadPoolExecutor
        from diag_tool.core.ssh_deploy import log_session_id

        nodes = list(self.nodes.values())
        print(f"[restart] pre-test: restarting all {len(nodes)} node(s) fresh", flush=True)
        with ThreadPoolExecutor(max_workers=len(nodes)) as ex:
            old_sessions = dict(zip(
                (rn.node_id for rn in nodes),
                ex.map(lambda rn: log_session_id(rn.hw), nodes),
            ))
            list(ex.map(self._stop, nodes))
            list(ex.map(lambda rn: self._start(rn, old_sessions[rn.node_id]), nodes))
        for rn in nodes:
            rn.start_tailing()
        ok = self.wait_operational(timeout=wait_operational)
        print(f"[restart] pre-test restart {'OK' if ok else 'FAILED'}", flush=True)
        return ok


def _make_hardware_fabric(request, expected_nodes: int):
    # Local imports: harness/hw_node.py and diag_tool.core.ssh_deploy
    # both live in this repo (pythonpath=src, see pytest.ini) but are
    # only needed in hardware mode -- no reason to import them (and
    # pull PySide6, an already-declared project dependency, into
    # import time) for a plain simulated run.
    from harness.diag import DiagClient
    from harness.hw_node import RemoteNode
    from diag_tool.core.ssh_deploy import enabled_nodes, nodes_from_json

    hw_nodes_file = request.config.getoption("--hw-nodes-file")
    if not hw_nodes_file:
        raise RuntimeError(
            "--fabric=hardware requires --hw-nodes-file=<path to JSON node "
            "list> (schema: diag_tool.core.ssh_deploy.HardwareNode). The "
            "diag tool's Test tab writes this automatically -- running "
            "pytest by hand needs it passed explicitly."
        )
    path = Path(hw_nodes_file)
    if not path.is_file():
        raise RuntimeError(f"--hw-nodes-file not found: {path}")
    configured = enabled_nodes(nodes_from_json(path.read_text()))
    if len(configured) < expected_nodes:
        raise RuntimeError(
            f"only {len(configured)} enabled hardware node(s) configured "
            f"in {path}, this scenario needs {expected_nodes}"
        )
    # Deterministic subset: lowest node_ids first -- matches the
    # simulated fabric_N fixtures' own_id range 0..N-1.
    chosen = configured[:expected_nodes]

    diag = DiagClient(
        multicast_group=request.config.getoption("--diag-group"),
        port=request.config.getoption("--diag-port"),
        interface_ip=request.config.getoption("--interface-ip"),
    )

    # Tails are NOT started here: restart_all() starts them once each
    # node has opened its new log session, see its docstring.
    nodes = {hn.node_id: RemoteNode(node_id=hn.node_id, hw=hn) for hn in chosen}
    fabric = _HardwareFabric(diag, nodes)

    # Fresh boot before every test -- see _HardwareFabric.restart_all()'s
    # docstring: without this, state left over from one scenario
    # (isolated peers, altered session_id/sequence counters, injected
    # faults) carries into the next scenario and breaks it, since the
    # nodes are otherwise only deployed once for the whole pytest run.
    #
    # This MUST happen before the diag-reachability check below, not
    # after: restart_all() works purely over SSH (stop_cmd/start_cmd),
    # so it doesn't care whether the node is currently alive, dead,
    # isolated, or in Failsafe. The diag-reachability check, in
    # contrast, requires the node to already be up and answering --
    # if a PREVIOUS scenario's fault injection killed a node's process
    # outright (rather than leaving it running-but-faulted), checking
    # reachability before restarting made every test after that one
    # fail immediately at discovery, restart_all() never even reached.
    restart_timeout = request.config.getoption("--discovery-timeout")
    try:
        ok = fabric.restart_all(wait_operational=restart_timeout)
    except Exception:
        fabric.stop_all()
        raise
    if not ok:
        fabric.stop_all()
        raise RuntimeError(
            f"hardware fabric: nodes did not reach operational within "
            f"{restart_timeout}s after the pre-test restart"
        )

    # NOW confirm every node also answers on the diag (UDP) channel --
    # the SSH-tailed log line restart_all() waited for proves the
    # framework's own CycleSync loop started, but scenario tests talk
    # to the nodes over this separate diag channel (fabric.diag.*), so
    # this is a genuinely distinct thing worth checking, just no longer
    # a precondition for restarting.
    deadline = time.monotonic() + request.config.getoption("--discovery-timeout")
    ready: set[int] = set()
    while time.monotonic() < deadline and len(ready) < len(chosen):
        for hn in chosen:
            if hn.node_id in ready:
                continue
            if diag.get_status(hn.node_id, timeout=0.3) is not None:
                ready.add(hn.node_id)
    if len(ready) < len(chosen):
        fabric.stop_all()
        raise RuntimeError(
            f"discovery: only {len(ready)}/{len(chosen)} configured hardware "
            f"nodes reachable via diag after restart (missing: "
            f"{sorted(hn.node_id for hn in chosen if hn.node_id not in ready)})"
        )

    return fabric


# ---- fabric_N fixtures ----------------------------------------------

@pytest.fixture
def fabric_3(request, binary, work_dir):
    if request.config.getoption("--fabric") == "hardware":
        f = _make_hardware_fabric(request, expected_nodes=3)
        yield f
        f.stop_all()
    else:
        f = _make_sim_fabric(binary, work_dir, nominal=3, minimum=2)
        yield f
        f.stop_all()


@pytest.fixture
def fabric_4(request, binary, work_dir):
    if request.config.getoption("--fabric") == "hardware":
        f = _make_hardware_fabric(request, expected_nodes=4)
        yield f
        f.stop_all()
    else:
        f = _make_sim_fabric(binary, work_dir, nominal=4, minimum=2)
        yield f
        f.stop_all()

