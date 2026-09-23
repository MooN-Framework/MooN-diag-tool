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
    configured+enabled hardware node from --hw-nodes-file, each
    already tailing its own --log-dir current-session log over SSH
    (see RemoteNode.start_tailing), so Node.wait_for_log() works the
    same as it does against a local subprocess.
    """
    def __init__(self, diag, nodes: dict[int, "RemoteNode"]):
        self.diag = diag
        self.nodes = nodes

    def alive_ids(self):
        return [nid for nid, n in self.nodes.items() if n.is_running()]

    def stop_all(self):
        # Only tears down the local SSH tail helpers + diag socket --
        # deliberately does NOT stop_cmd the remote node processes at
        # TEARDOWN. Hardware nodes are pushed/deployed once per pytest
        # session (the diag tool's "Deploy binary + config before run"
        # step), so leaving the binaries running here is fine -- the
        # NEXT test's fixture setup calls restart_all() anyway, which
        # gives it a fresh boot regardless of what state this test left
        # behind. Stopping here too would just be redundant work.
        try:
            self.diag.close()
        except Exception:
            pass
        for node in self.nodes.values():
            node._stop_tailing()

    def restart_node(self, node_id: int, wait_operational: float = 8.0) -> None:
        """
        Stop + start the real remote node process via SSH
        (resolved_stop_cmd / resolved_start_cmd). The node's tail
        subprocess is left running throughout -- `tail -F` reopens the
        current-log symlink by path once the new session's file
        appears, so the same RemoteNode keeps streaming log lines
        across the restart (see harness/hw_node.py docstring). No
        wait_operational check, same as the simulated Fabric: the
        rejoin flow runs over ResyncLostPeer, not CycleSyncOk, so the
        test itself should wait via wait_peer_health(..., "Probation").
        """
        if node_id not in self.nodes:
            raise KeyError(f"unknown node_id {node_id}")
        from diag_tool.core.ssh_deploy import SshError, ssh_exec  # local import, see below

        rn = self.nodes[node_id]
        print(f"[restart] node {node_id} ({rn.hw.host}): stopping", flush=True)
        try:
            ssh_exec(rn.hw, rn.hw.resolved_stop_cmd(), timeout=15.0)
        except SshError:
            pass  # best-effort, matches simulated Fabric.restart_node's "kill if still alive"
        time.sleep(0.3)  # let the old process actually exit before rebinding its sockets
        print(f"[restart] node {node_id} ({rn.hw.host}): starting", flush=True)
        start_out = ssh_exec(rn.hw, rn.hw.resolved_start_cmd(), timeout=15.0)
        if start_out.strip():
            # A failed redirect in the backgrounded job is only visible
            # here (rc stays 0), see ssh_deploy.ensure_remote_dirs().
            print(f"[restart] node {node_id} ({rn.hw.host}): start_cmd output: "
                  f"{start_out.strip()}", flush=True)
        _ = wait_operational  # reserved, mirrors the simulated Fabric

    def wait_operational(self, timeout: float = 15.0, after: Optional[dict[int, int]] = None) -> bool:
        """Same detection pattern as the simulated Fabric.wait_operational:
        each node's current-session log (tailed live over SSH -- see
        RemoteNode.start_tailing/wait_for_log) prints this line once
        discovery + sync finished and the first cycle is running.

        `after`: optional {node_id: line_count} watermark (see
        RemoteNode.line_count), one per node, restricting the match to
        lines produced since that point. Without it, a pattern that
        already matched once earlier in this pytest session (e.g. the
        very first successful boot) matches that same stale buffered
        line again instantly -- see restart_all()'s docstring for why
        that's a real bug, not a theoretical one.
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
        """Stop + start EVERY managed node fresh, then wait for all of
        them to reach the first operational cycle.

        Hardware nodes are only pushed/deployed ONCE per pytest session
        (the diag tool's "Deploy binary + config before run" step, see
        stop_all()'s docstring) -- without restarting the
        already-running binaries between tests, state left over from
        one scenario (isolated peers, altered session_id/sequence
        counters, injected faults) carries straight into the next one
        and breaks it. Call this once per test, from the fabric_N
        fixture's setup, mirroring the simulated Fabric's fresh
        start_all() + wait_operational() every test gets for free.

        Captures each node's line_count() watermark BEFORE restarting
        it and passes it through to wait_operational(after=...) --
        without this, the readiness check matches the FIRST test's
        "CycleSyncOk" line (still sitting in the tail buffer) on every
        subsequent restart and reports success immediately, without the
        new session having actually come up yet. That made every test
        after the first one run against a system that wasn't really
        ready, even though restart_all() itself ran correctly.
        """
        watermarks: dict[int, int] = {}
        print(f"[restart] pre-test: restarting all {len(self.nodes)} node(s) fresh", flush=True)
        for node_id, node in self.nodes.items():
            watermarks[node_id] = node.line_count
            self.restart_node(node_id)
        ok = self.wait_operational(timeout=wait_operational, after=watermarks)
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

    nodes: dict[int, RemoteNode] = {}
    for hn in chosen:
        rn = RemoteNode(node_id=hn.node_id, hw=hn)
        rn.start_tailing()
        nodes[hn.node_id] = rn
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
    if not fabric.restart_all(wait_operational=restart_timeout):
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

