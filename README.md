# MooN Diagnostic Tool + Test Framework

Single repository containing both the diagnostic PySide6 GUI and the
pytest-based test framework for the MooN voting system.

## Repo layout

```
.
├── pyproject.toml         # uv-managed, installs both packages
├── pytest.ini             # markers, testpaths=tests, pythonpath=src
├── conftest.py            # session fixtures (--rust-repo, --fabric=...)
├── src/
│   ├── diag_tool/         # PySide6 GUI package
│   └── harness/           # reusable test harness (Fabric, Node, DiagClient, ...)
├── tests/
│   ├── scenarios/         # scenario tests (test_t01 .. test_t38)
│   └── timing_analysis/   # standalone timing study
```

The GUI and the test harness share the same repo so:
- the timing sweep in the GUI can reuse `harness.config_gen.make_spec`
  and generate the exact same TOML the tests use,
- the Test tab in the GUI can launch pytest against `tests/scenarios/`
  without any path juggling,
- future refactors of the wire format touch one place instead of two.

The Rust node source stays in its own repo. Point at it via the
`--rust-repo=PATH` option to pytest (or set `RUST_REPO` in the
environment); the GUI reads it from the "Rust repository" setting in
Settings.

## Setup

```bash
uv sync
uv run diag-tool          # launch GUI
```

Or without uv:

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e '.[tests]'
diag-tool
```

### System dependencies (hardware mode)

Configuring any hardware node (Test tab / Timing tab → "Configure
hardware nodes…") switches those tabs into hardware mode and pulls in
two external tools that are **not** Python packages and won't get
installed by `uv sync`/`pip install`:

- **`sshpass`** — required for every SSH/SCP call the tool makes
  (deploy, start/stop, log fetch, the "Test" reachability probe).
  Auth is password-based (matches the nodes' test-network SSH
  config), and `ssh`/`scp` can't take a password non-interactively on
  their own, so `sshpass` drives the prompt. Without it, every
  hardware action fails immediately with a clear "sshpass not found"
  error instead of hanging.
  Install: `apt install sshpass` (Debian/Ubuntu) or the equivalent for
  your distro.
- **`cross`** (+ Docker or Podman) — only needed if you set a "Target
  triple" different from the host (e.g. `aarch64-unknown-linux-gnu`
  for the Pi nodes) in the Timing tab's cross-compile panel. Host
  builds (empty triple) use plain `cargo` and don't need this.
  Install: `cargo install cross --locked`, plus a running Docker or
  Podman daemon.

Linux-only by design — no Windows/WSL2 support layer is planned; run
the tool on the same Linux box (or one on the same LAN) as the nodes.

## Running the tests

```bash
# simulated (cargo build + local subprocess nodes)
pytest --rust-repo=../voting-node

# hardware (SSH-restart of already-installed nodes)
pytest --fabric=hardware \
       --diag-group=239.10.0.2 --diag-port=6666 \
       --op-group=239.10.0.1 --op-port=5555 \
       --interface-ip=192.168.1.1

# just the timing sweep
pytest -m timing --rust-repo=../voting-node
```

The GUI's **Tests** tab wraps all of this: mode follows automatically
from whether any hardware node is configured, pick scenarios, click
Start.

## GUI tabs

| Tab      | Purpose                                                                       |
|----------|-------------------------------------------------------------------------------|
| Status   | Live overview of all discovered nodes (state, seq, peers, frame count)        |
| Inject   | Injection commands with multi-target selection and a preset menu for the tests|
| Log      | Live feed of all telegrams (op + diag), filters, optional JSONL session log, "Open logs folder" |
| Tests    | pytest runner against `tests/scenarios/`; mode (`simulated`/`hardware`) is derived from the configured hardware node count, not chosen manually |
| Timing   | Cycle-duration sweep with cross-compile + SSH deploy, on host or hardware     |
| Settings | Multicast config, paths, "Open logs folder"                                  |

### Mode: simulated vs hardware

Both the Tests and Timing tab derive their mode from the hardware node
list (shared between the two, edited via "Configure hardware
nodes…"): **0 nodes configured → simulated** (pytest spawns nodes
locally via `cargo build` + subprocess), **≥1 node configured →
hardware** (uses the configured nodes over SSH + the multicast
groups). There's no separate manual switch to forget to flip.

### Timing tab

Runs a descending sweep over `cycle_duration_ms` candidates. For each
candidate the tool spins the fabric up (locally or via SSH), waits for
operational via diag, measures STATE-frame inter-arrival times for a
configurable window, and grades the result STABLE / TOO_MANY_OVERRUNS
/ NO_OPERATIONAL / NODE_DIED. Measurement is purely passive on the
operational multicast — no log parsing.

The tab also has a cross-compile panel and a "Deploy to all hardware
nodes" button that SCPs the produced binary to each configured node.
Host builds (empty "Target triple") run plain `cargo build --release`;
setting a target triple (e.g. `aarch64-unknown-linux-gnu`) switches to
`cross build --release --target=<triple>`, which builds inside a
Docker/Podman container with the matching toolchain already in it —
see "System dependencies" above. Hardware nodes are edited in the
"Configure hardware nodes…" dialog and persisted in the app settings.

Node start/stop is plain process management, not systemd: default
start command is `nohup {bin} --config {cfg} > {log} 2>&1 < /dev/null
& disown`, default stop is `pkill -f {bin} || true`. Both are
per-node editable strings (`{bin}`/`{cfg}`/`{log}` placeholders) in
case a future deployment does want a systemd unit or something else —
nothing is hardcoded to this default.

SSH uses the system `ssh`/`scp` binaries through `sshpass` with
password auth (matches the nodes' SSH config on the test network) —
see "System dependencies" above for the `sshpass` requirement. There's
a "Fetch + open node logs" action next to "Configure hardware
nodes…" that SCPs each node's `{log}` file back and opens the local
folder it landed in.

## Hardware mode internals

This section is for anyone running `pytest` by hand against real
nodes, or who wants to know what's actually happening underneath the
Test tab's hardware mode.

### What the Test tab passes to pytest

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

### `_HardwareFabric` / `harness.hw_node.RemoteNode`

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

### `restart_node()` on hardware

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

### Fixture teardown vs. `restart_node()`

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

### What still doesn't apply to hardware

- The `binary` fixture returns `None` in hardware mode — no local
  cargo build, nodes are already flashed/deployed.
- `wait_node_died` was always mode-agnostic (polls `GetStatus` via
  `fabric.diag`), nothing hardware-specific about it.
- A scenario needing more nodes than are currently configured+enabled
  stays infeasible — see `core/scenario_meta.py` /
  `infeasible_reason()`, surfaced in the Test tab's scenario list as
  greyed-out items.

## Test scenario coverage

`tests/scenarios/` contains 38 scenarios (`test_t01`…`test_t38`)
covering fault injection, timeout edges, quorum limits, rejoin/
probation handling, and the PeerInError rendezvous mechanism against
the MooN voting framework, exercised in both 2oo3 and 2oo4
configurations. Each scenario file's docstring
documents its setup, the injected fault, and the expected framework
behaviour in detail.

There is no `T39`: it injected the same single forged EM header as
`T38` with the same assertions and differed only in whether the
docstring called it an accident or an attack, so the two could
disagree on pure timing within one run. They are merged into `T38`,
which now repeats the injection and reports how many attempts the
fabric survives. IDs are not renumbered, so the gap is deliberate.

A handful of scenarios (`T30`–`T34`) are marked `pytest.mark.skip`:
they target timeout edges that turned out not to be reliably,
deterministically triggerable with the current injection API and
framework internals. Each one documents why, what was tried, and a
concrete implementation path if the edge needs to be covered later —
see the individual docstrings. `T14` is skipped for a similar reason
(needs a dedicated Rust-side frame-delay injection). These are
intentionally left in the suite as documented open points rather than
deleted, since running the suite still surfaces them as skipped
(not silently absent). Each of the six defines a real test function
under its module-level skip, so pytest collects and reports it
instead of exiting 5 on a file with zero items.

`T17` and `T22` cannot run against hardware and say so declaratively
via a module-level `HW_UNSUPPORTED = "<reason>"` constant, which
`scenario_meta` reads (with `ast`, without importing the module) so
the Test tab can grey them out before a run. Both keep their runtime
`pytest.skip` as a fallback for hand-run pytest invocations.

The timing study under `tests/timing_analysis/` is excluded from a
normal run by `addopts = -m "not timing"` in `pytest.ini`. Run it
explicitly with `pytest -m timing`. The `slow` marker is NOT
deselected automatically; use `pytest -m "not slow"` for a fast run.
