"""
Pytest fixtures for the 2oo3 test framework.

Sitzt im Repo-Root, wird von pytest automatisch geladen.

CLI-Optionen:
  --rust-repo=PATH      Wo liegt das Rust-Node-Repo (Cargo.toml). Default:
                        env var RUST_REPO oder ../voting-node relativ zum Repo.
  --fabric=MODE         "simulated" (default) baut lokal via cargo und
                        spawnt Prozesse; "hardware" verwendet reale Nodes
                        ueber die konfigurierten Multicast-Gruppen.
  --diag-group / --diag-port / --op-group / --op-port / --interface-ip
                        Multicast-Konfig fuer Hardware-Modus.

Wichtige Fixtures:
- `binary` (session-scope): pfad zum kompilierten Rust-Node-Binary.
  Baut mit `cargo build --features diagnostic` beim ersten Test, danach cached.
- `work_dir` (function-scope): temp-Verzeichnis pro Test.
- `fabric_3` / `fabric_4` (function-scope): Fabric-Fixtures, im
  Hardware-Modus ersetzt durch einen Discovery-basierten Adapter.
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
    Spawns nothing -- waits for the expected number of nodes to appear
    on the diag channel via passive discovery, then exposes the same
    interface (diag, nodes.keys(), stop_all).

    Tests that rely on Fabric.restart_node / Node.wait_for_log /
    Node.is_running degrade gracefully (see _NullNode) or should skip
    themselves in hardware mode via `@pytest.mark.skipif`.
    """
    def __init__(self, diag, expected_ids):
        self.diag = diag
        self.nodes = {nid: _NullNode(nid) for nid in expected_ids}

    def alive_ids(self):
        return [nid for nid, n in self.nodes.items() if n.is_running()]

    def stop_all(self):
        try:
            self.diag.close()
        except Exception:
            pass

    def restart_node(self, node_id, wait_operational=8.0):
        raise NotImplementedError(
            "restart_node is not implemented in hardware mode -- "
            "restart the node manually."
        )


class _NullNode:
    def __init__(self, node_id):
        self.node_id = node_id
        self._alive = True
    def is_running(self):
        return self._alive
    def wait_for_log(self, *_a, **_kw):
        return None


def _make_hardware_fabric(request, expected_nodes: int):
    from harness.diag import DiagClient  # local import; harness is on pythonpath

    diag = DiagClient(
        multicast_group=request.config.getoption("--diag-group"),
        port=request.config.getoption("--diag-port"),
        interface_ip=request.config.getoption("--interface-ip"),
    )
    deadline = time.monotonic() + request.config.getoption("--discovery-timeout")
    ids: set[int] = set()
    while time.monotonic() < deadline and len(ids) < expected_nodes:
        # Probe a generous range of candidate node ids.
        for candidate in range(0, max(expected_nodes * 4, 8)):
            if candidate in ids:
                continue
            if diag.get_status(candidate, timeout=0.3) is not None:
                ids.add(candidate)
                if len(ids) >= expected_nodes:
                    break
    if len(ids) < expected_nodes:
        try:
            diag.close()
        except Exception:
            pass
        raise RuntimeError(
            f"discovery: only {len(ids)}/{expected_nodes} nodes reachable "
            f"(found: {sorted(ids)})"
        )
    return _HardwareFabric(diag, sorted(ids))


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
