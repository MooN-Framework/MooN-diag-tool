# Hardware mode for the test harness

`conftest.py` supports `--fabric=hardware` to run the scenarios in
`tests/scenarios/` against real, already-deployed nodes instead of
locally spawned processes. The diag tool's Test tab drives this
automatically (mode is derived from whether hardware nodes are
configured, see `HardwareNodesDialog`); this doc is for anyone running
`pytest` by hand or wanting to know what's actually happening
underneath.

## What the Test tab passes to pytest

```
--fabric=hardware
--hw-nodes-file=<tmp>.json
--diag-group=... --diag-port=... --op-group=... --op-port=... --interface-ip=...
```

`--hw-nodes-file` points at a JSON file (schema:
`diag_tool.core.ssh_deploy.HardwareNode` — host, user, port, password,
`remote_binary`/`remote_config`/`remote_log`/`remote_log_dir`,
`start_cmd`/`stop_cmd`) with the *enabled* nodes from "Configure
hardware nodes…". The Test tab writes it fresh before every hardware
run (0600, via `write_hw_nodes_file`) and deletes it again once pytest
exits, successfully or not — see `TestTab._run_worker` /
`_run_worker_inner`. Running pytest manually needs this flag passed
explicitly; without it `--fabric=hardware` fails fast with a clear
error instead of silently falling back to degraded stubs.

## `_HardwareFabric` / `harness.hw_node.RemoteNode`

`conftest.py::_make_hardware_fabric`:
1. Loads the enabled nodes from `--hw-nodes-file`, takes the lowest
   `node_id`s up to the scenario's required count (`fabric_3`/
   `fabric_4`).
2. Confirms each one actually answers `GetStatus` on the diag channel
   within `--discovery-timeout` (no more blind candidate-id scanning
   — the node list is known up front now).
3. Builds one `harness.hw_node.RemoteNode` per node and calls
   `start_tailing()` on each.

`RemoteNode.start_tailing()` spawns a persistent
`ssh ... tail -n +1 -F <remote_log_dir>/node_<id>_current.log`
subprocess (`core.ssh_deploy.spawn_tail_process`) and reads its stdout
line-by-line into the same deque-backed buffer `harness.node.Node`
uses locally. This requires the framework's `--log-dir` file-logging
support (added for HW deployment) — the node binary needs to have
actually been started with `--log-dir` for `node_<id>_current.log` to
exist. `HardwareNode`'s default `start_cmd` does this already:

```
nohup {bin} --config {cfg} --log-dir {log_dir} > {log} 2>&1 < /dev/null & disown
```

The result: `RemoteNode` exposes the exact same
`wait_for_log()` / `log_lines` / `is_running()` / `stop()` /
`node_id` API as the local-subprocess `Node`, so
`harness/assertions.py` (`any_node_reached_failsafe`,
`assert_exclusion_confirmed`) and a scenario's own direct
`fabric_3.nodes[id].wait_for_log(...)` calls work unmodified against
hardware.

## `restart_node()` on hardware

`_HardwareFabric.restart_node(node_id)` runs `resolved_stop_cmd()`
then `resolved_start_cmd()` over SSH on the real remote process — an
actual power-cycle of that node, not a simulation. The node's
`RemoteNode` tail subprocess is **not** touched or respawned: `tail
-F` (capital F, "follow retry") re-opens `node_<id>_current.log` by
path once the framework repoints that symlink at the new session's
file on the next start, so the same tail process — and the same
`RemoteNode` object a test is holding — keeps streaming lines across
the restart. That's what makes `test_t13_rejoin.py`-style scenarios
(`fabric_3.restart_node(TARGET)` followed by
`fabric_3.nodes[OBSERVER].wait_for_log(...)`) work the same way in
both modes.

## Fixture teardown vs. `restart_node()`

`_HardwareFabric.stop_all()` (called on every `fabric_3`/`fabric_4`
fixture teardown, i.e. after every single test) only closes the diag
socket and terminates the local SSH tail helpers — it deliberately
does **not** run `stop_cmd` on the remote nodes. Hardware nodes are an
external, already-running resource shared across the whole pytest
session (started once by the Test tab's "Deploy binary + config
before run" step before pytest launches at all, not per-test like
simulated nodes); killing them after each test would break the next
one. `restart_node()` is the explicit, per-test operation for a
scenario that actually wants a node to go down and come back.

## What still doesn't apply to hardware

- The `binary` fixture returns `None` in hardware mode — no local
  cargo build, nodes are already flashed/deployed.
- `wait_node_died` was always mode-agnostic (polls `GetStatus` via
  `fabric.diag`), nothing hardware-specific about it.
- A scenario needing more nodes than are currently configured+enabled
  stays infeasible — see `core/scenario_meta.py` /
  `infeasible_reason()`, surfaced in the Test tab's scenario list as
  greyed-out items.
