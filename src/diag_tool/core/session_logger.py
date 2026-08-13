"""
Persists every received telegram of a session to a JSONL file.

One file per session, format:
    {"ts": <mono_ns>, "ts_wall": <ISO>, "ch": "op"|"diag",
     "raw": {...} | {"decoded": {...}, "hex": "..."}}
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from .wire_decoder import DecodedFrame


class SessionLogger:
    def __init__(self, base_dir: Path) -> None:
        self.base_dir = Path(base_dir)
        self._fh = None
        self._lock = threading.Lock()
        self.path: Optional[Path] = None

    def start(self) -> Path:
        with self._lock:
            self.base_dir.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            self.path = self.base_dir / f"session_{stamp}.jsonl"
            self._fh = self.path.open("w", buffering=1)  # line-buffered
            return self.path

    def stop(self) -> None:
        with self._lock:
            if self._fh is not None:
                self._fh.close()
                self._fh = None

    def is_active(self) -> bool:
        return self._fh is not None

    def _write(self, entry: dict[str, Any]) -> None:
        with self._lock:
            if self._fh is None:
                return
            entry["ts_wall"] = datetime.now(timezone.utc).isoformat()
            try:
                self._fh.write(json.dumps(entry, default=str) + "\n")
            except (OSError, TypeError):
                pass

    def log_operational(self, frame: DecodedFrame, ts_mono: float) -> None:
        d = {k: getattr(frame, k) for k in frame.__slots__}
        # bytes is not JSON-serialisable -> hex
        d["body_raw"] = frame.body_raw.hex()
        self._write({"ts": ts_mono, "ch": "op", "decoded": d})

    def log_diag(self, raw: dict, ts_mono: float) -> None:
        self._write({"ts": ts_mono, "ch": "diag", "raw": raw})
