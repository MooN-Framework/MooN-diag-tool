# 2oo3 Diagnostic Tool + Test Framework

Single repository containing both the diagnostic PySide6 GUI and the
pytest-based test framework for the SIL-2 2oo3 voting system.

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
│   ├── scenarios/         # scenario tests (test_t01 .. test_t19)
│   └── timing_analysis/   # standalone timing studies (T20)
└── docs/
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
