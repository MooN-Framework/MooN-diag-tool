# 2oo3 Diagnostic Tool

PySide6 GUI for the SIL-2 2oo3 voting framework: live monitoring,
fault injection, session logging, and a test runner (simulated and
real hardware).

## Setup with uv

```bash
uv sync
uv run diag-tool
```

## Without uv

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .
diag-tool
```

## Or run directly

```bash
pip install PySide6
PYTHONPATH=src python -m diag_tool.app
```

## Tabs

| Tab      | Purpose                                                                       |
|----------|-------------------------------------------------------------------------------|
| Status   | Live overview of all discovered nodes (state, seq, peers, frame count)        |
| Inject   | Injection commands with multi-target selection and a preset menu for the tests|
| Log      | Live feed of all telegrams (op + diag), filters, optional JSONL session log   |
| Tests    | pytest runner against `scenarios/`, modes `simulated` / `hardware`            |
| Settings | Multicast config, paths, test mode                                            |

## Architecture

- `core/wire_decoder.py` — binary frames (23-byte header + CRC32)
- `core/diag_client.py` — JSON diag client, sender + rx thread
- `core/operational_listener.py` — passive binary listener
- `core/node_registry.py` — thread-safe auto-discovery
- `core/session_logger.py` — JSONL per session
- `core/settings.py` — persistent application state
- `ui/main_window.py` — wires everything, reconnects on settings change
- `ui/*_tab.py` — tabs, each independently testable

Nodes are discovered at runtime — the node count is not hard-coded
anywhere. Both operational frames (each carrying its `node_id`) and
diag replies (`source_node_id`) populate the registry.

## Hardware mode

See [`docs/harness_hardware.md`](docs/harness_hardware.md) for the
one-time `conftest.py` patch that enables `--fabric=hardware`. The
tool then calls pytest with the multicast parameters from Settings,
without spawning nodes locally.

## Ideas for later

- Timeline widget on the Status tab (state transitions per node as
  colour stripes)
- Diff view between two get_status snapshots
- Result payload decoder (currently just a hex dump for RESULT frames)
- CRC error counter per node in the status table
- Export of the log buffer as CSV
