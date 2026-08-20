"""
Pytest fixtures for the 2oo3 test framework.

Sitzt im Repo-Root, wird von pytest automatisch geladen.

CLI-Optionen:
  --rust-repo=PATH      Wo liegt das Rust-Node-Repo (Cargo.toml). Default:
                        env var RUST_REPO oder ../voting-node relativ zum Repo.
  --fabric=MODE         "simulated" (default) baut lokal via cargo und
                        spawnt Prozesse; "hardware" verwendet reale Nodes
                        ueber die konfigurierten Multicast-Gruppen.
  --hw-nodes-file=PATH  Nur --fabric=hardware: Pfad zu einer JSON-Datei mit
                        der Node-Verbindungsdaten-Liste (Schema wie
                        diag_tool.core.ssh_deploy.HardwareNode -- host, user,
                        port, password, remote_binary/config/log/log_dir,
                        start_cmd/stop_cmd). Der Diagnose-Tool-Test-Tab
                        schreibt diese Datei automatisch (0600, temp) vor
                        jedem Hardware-Lauf und raeumt sie danach auf. Ohne
                        diese Datei ist im Hardware-Modus weder
                        restart_node() noch Node.wait_for_log() moeglich,
                        siehe harness/hw_node.py.
  --diag-group / --diag-port / --op-group / --op-port / --interface-ip
                        Multicast-Konfig fuer Hardware-Modus.

Wichtige Fixtures:
- `binary` (session-scope): pfad zum kompilierten Rust-Node-Binary.
  Baut mit `cargo build --features diagnostic` beim ersten Test, danach cached.
- `work_dir` (function-scope): temp-Verzeichnis pro Test.
- `fabric_3` / `fabric_4` (function-scope): Fabric-Fixtures, im
  Hardware-Modus ersetzt durch einen `--hw-nodes-file`-basierten Adapter
  (siehe _HardwareFabric) mit echtem restart_node() und log-basierten
  wait_for_log()-Assertions ueber das Framework's --log-dir-Filelogging.
"""
from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

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
        # deliberately does NOT stop_cmd the remote node processes.
        # Hardware nodes are an external, already-running resource
        # shared across every test in this pytest session (started
        # once by the diag tool's "Deploy binary + config before run"
        # step, not per-fixture like simulated nodes), so a function-
        # scoped fabric_N fixture tearing them down after one test
        # would break the next one.
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
        try:
            ssh_exec(rn.hw, rn.hw.resolved_stop_cmd(), timeout=15.0)
        except SshError:
            pass  # best-effort, matches simulated Fabric.restart_node's "kill if still alive"
        time.sleep(0.3)  # let the old process actually exit before rebinding its sockets
        ssh_exec(rn.hw, rn.hw.resolved_start_cmd(), timeout=15.0)
        _ = wait_operational  # reserviert, wie im simulierten Fabric


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

    # Confirm every configured node actually answers on the diag
    # channel before handing the fabric to the test -- scoped to the
    # exact configured ids now (no more blind candidate-range scan).
    deadline = time.monotonic() + request.config.getoption("--discovery-timeout")
    ready: set[int] = set()
    while time.monotonic() < deadline and len(ready) < len(chosen):
        for hn in chosen:
            if hn.node_id in ready:
                continue
            if diag.get_status(hn.node_id, timeout=0.3) is not None:
                ready.add(hn.node_id)
    if len(ready) < len(chosen):
        try:
            diag.close()
        except Exception:
            pass
        raise RuntimeError(
            f"discovery: only {len(ready)}/{len(chosen)} configured hardware "
            f"nodes reachable via diag (missing: "
            f"{sorted(hn.node_id for hn in chosen if hn.node_id not in ready)})"
        )

    nodes: dict[int, RemoteNode] = {}
    for hn in chosen:
        rn = RemoteNode(node_id=hn.node_id, hw=hn)
        rn.start_tailing()
        nodes[hn.node_id] = rn
    return _HardwareFabric(diag, nodes)


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

