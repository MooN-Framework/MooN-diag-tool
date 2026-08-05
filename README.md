# diag-tool

PySide6-basiertes Diagnosetool für das 2oo3-Voting-System (Master-Thesis).

## Features (Gerüst)

- **Live-Monitoring**: Node-States, letztes Voting-Ergebnis
- **Fault Injection**: manueller Frame-Editor + geplante Presets
- **Session-Log**: strukturierte JSONL-Dateien pro Session
- **Einstellungen**: Multicast-Endpunkte, Logging-Verzeichnis
- **Zwei Multicast-Schnittstellen**: passiv (Betrieb) + bidirektional (Diagnose)

## Setup

```bash
uv sync
uv run diag-tool
```

## Entwicklung

```bash
uv run ruff check src tests
uv run mypy src
uv run pytest
```

## Struktur

```
src/diag_tool/
├── app.py             QApplication-Bootstrap
├── config.py          pydantic-settings + TOML
├── net/
│   ├── frame.py       UdpFrame-Wireformat (STUB — mit Rust abgleichen)
│   ├── listener.py    passiver Multicast-Worker
│   └── command.py     bidirektionaler Command-Worker
├── core/
│   ├── models.py      Domänenobjekte
│   ├── session.py     Session-Lifecycle
│   └── session_log.py JSONL-Session-Log
└── ui/
    ├── main_window.py
    ├── monitor_view.py
    ├── inject_view.py
    ├── log_view.py
    └── settings_dialog.py
```

## Konfiguration

`$XDG_CONFIG_HOME/diag-tool/config.toml` (default `~/.config/diag-tool/config.toml`) — Beispiel:

```toml
[network.system]
group = "239.10.0.1"
port = 30001
interface = "0.0.0.0"

[network.diagnose]
group = "239.10.0.2"
port = 30002
interface = "0.0.0.0"
ttl = 1

[logging]
session_dir = "~/.local/share/diag-tool/sessions"
```

Alternativ per Umgebungsvariable: `DIAG_NETWORK__SYSTEM__PORT=31000` etc.

## Session-Logs

Jede Session schreibt eine JSONL-Datei nach `$XDG_DATA_HOME/diag-tool/sessions/session_<UTC-Timestamp>.jsonl`.

## TODO

- `net/frame.py` an das reale Rust-Wireformat anpassen (Feld-Layout, Byte-Order, CRC)
- Payload-Dekodierung pro `FrameKind` in `core/models.py` verankern
- Fault-Injection-Presets (Drop / Delay / Byzantinisch) implementieren
- Bremskurven-Plot in `MonitorView` (matplotlib oder pyqtgraph)
- Replay eines Session-Logs
