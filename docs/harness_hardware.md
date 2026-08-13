# Hardware mode for the test harness

To let the diagnostic tool run the existing `scenarios/*` against real
hardware, `conftest.py` needs to know a `--fabric` mode. The patch is
minimally invasive: the existing `fabric_3` and `fabric_4` fixtures
fall back to a lightweight discovery wrapper in hardware mode, without
spawning any processes.

## One-time patch for `conftest.py`

```python
# --- CLI options -----------------------------------------------------

def pytest_addoption(parser):
    parser.addoption("--fabric", default="simulated",
                     choices=["simulated", "hardware"])
    parser.addoption("--diag-group", default="239.10.0.2")
    parser.addoption("--diag-port",  default=6666, type=int)
    parser.addoption("--op-group",   default="239.10.0.1")
    parser.addoption("--op-port",    default=5555, type=int)
    parser.addoption("--interface-ip", default="127.0.0.1")
    parser.addoption("--discovery-timeout", default=10.0, type=float)


# --- Hardware fabric adapter ----------------------------------------

class HardwareFabric:
    """
    Duck-typed drop-in for `harness.fabric.Fabric` in hardware mode.
    Does not spawn any processes -- waits for the expected number of
    nodes to show up on the diag channel instead.
    """
    def __init__(self, diag, expected_ids):
        self.diag = diag
        self.nodes = {nid: _NullNode(nid) for nid in expected_ids}

    def alive_ids(self):
        return [nid for nid, n in self.nodes.items() if n.is_running()]

    def stop_all(self):
        self.diag.close()

    def restart_node(self, node_id, wait_operational=8.0):
        raise NotImplementedError("restart_node is not implemented in "
                                  "hardware mode -- restart the node "
                                  "manually.")

class _NullNode:
    def __init__(self, node_id):
        self.node_id = node_id
        self._alive = True
    def is_running(self):
        # Hardware mode: no local process to poll. Tests that check for
        # process death (wait_node_died) should instead check for missing
        # diag responses after a shutdown injection. For simple tests:
        # leave True.
        return self._alive
    def wait_for_log(self, *_a, **_kw):
        # No local log file available. Tests that rely on log patterns
        # (any_node_reached_failsafe) must, in hardware mode, use the
        # diag status instead.
        return None


def _make_hardware_fabric(request, expected_nodes):
    from harness.diag import DiagClient
    diag = DiagClient(
        multicast_group=request.config.getoption("--diag-group"),
        port=request.config.getoption("--diag-port"),
        interface_ip=request.config.getoption("--interface-ip"),
    )
    # Discovery: poll get_status on every plausible node_id until we
    # have expected_nodes. Alternatively: listen passively on op multicast.
    import time
    ids = set()
    deadline = time.monotonic() + request.config.getoption("--discovery-timeout")
    while time.monotonic() < deadline and len(ids) < expected_nodes:
        for candidate in range(0, expected_nodes * 4):  # try generously
            if candidate in ids:
                continue
            if diag.get_status(candidate, timeout=0.3) is not None:
                ids.add(candidate)
                if len(ids) >= expected_nodes:
                    break
    if len(ids) < expected_nodes:
        diag.close()
        raise RuntimeError(
            f"Discovery: only {len(ids)}/{expected_nodes} nodes reachable "
            f"(found: {sorted(ids)})"
        )
    return HardwareFabric(diag, sorted(ids))


# --- fabric_N fixtures ----------------------------------------------

@pytest.fixture
def fabric_3(request, binary, work_dir):
    if request.config.getoption("--fabric") == "hardware":
        f = _make_hardware_fabric(request, expected_nodes=3)
        yield f
        f.stop_all()
        return
    # otherwise as before (subprocess):
    f = _make_fabric(binary, work_dir, nominal=3, minimum=2)
    yield f
    f.stop_all()


@pytest.fixture
def fabric_4(request, binary, work_dir):
    if request.config.getoption("--fabric") == "hardware":
        f = _make_hardware_fabric(request, expected_nodes=4)
        yield f
        f.stop_all()
        return
    f = _make_fabric(binary, work_dir, nominal=4, minimum=2)
    yield f
    f.stop_all()


# --- binary fixture skips the cargo build in hardware mode ----------

@pytest.fixture(scope="session")
def binary(request):
    if request.config.getoption("--fabric") == "hardware":
        # cargo build is unnecessary -- the nodes are already running.
        return None
    # otherwise as before
    ...
```

## What tests can (and cannot) do in hardware mode

**Works out of the box:**
- All tests that only use `fabric.diag` calls plus `wait_node_state`,
  `wait_peer_health`, `wait_cycles_advance`.
- All injection commands.

**Needs adaptation or should be skipped:**
- `wait_node_died` — no `Popen.poll` in hardware mode. Replace with:
  after a `shutdown` injection, wait for `get_status` to time out.
- `any_node_reached_failsafe` (log pattern) — no local log. Replace with
  `wait_node_state(fabric, nid, "Failsafe")`.
- `restart_node` — the node has to be restarted physically.

A `pytest.mark`-based skip for hardware-incompatible tests helps here.
