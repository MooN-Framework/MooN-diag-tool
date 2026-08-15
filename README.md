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

The GUI's **Tests** tab wraps all of this: pick a mode, pick
scenarios, click Start.

## GUI tabs

| Tab      | Purpose                                                                       |
|----------|-------------------------------------------------------------------------------|
| Status   | Live overview of all discovered nodes (state, seq, peers, frame count)        |
| Inject   | Injection commands with multi-target selection and a preset menu for the tests|
| Log      | Live feed of all telegrams (op + diag), filters, optional JSONL session log   |
| Tests    | pytest runner against `tests/scenarios/`, modes `simulated` / `hardware`      |
| Timing   | Cycle-duration sweep with cross-compile + SSH deploy, on host or hardware     |
| Settings | Multicast config, paths, test mode                                            |

### Timing tab

Runs a descending sweep over `cycle_duration_ms` candidates. For each
candidate the tool spins the fabric up (locally or via SSH), waits for
operational via diag, measures STATE-frame inter-arrival times for a
configurable window, and grades the result STABLE / TOO_MANY_OVERRUNS
/ NO_OPERATIONAL / NODE_DIED. Measurement is purely passive on the
operational multicast — no log parsing.

The tab also has a cross-compile panel (`cargo build --release
--target=<triple>`) and a "Deploy to all hardware nodes" button that
SCPs the produced binary to each configured node. Hardware nodes are
edited in the "Configure hardware nodes…" dialog and persisted in the
app settings.

SSH uses the system `ssh` and `scp` binaries with `BatchMode=yes`, so
key-based authentication is required — set up ssh-agent or point at a
key file in the node config.
