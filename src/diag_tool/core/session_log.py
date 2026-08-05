"""Session-Logging — JSONL pro Session in ``$XDG_DATA_HOME/diag-tool/sessions/``.

Jede Diag-Session bekommt eine Datei ``session_<ISO-Timestamp>.jsonl``. Events
werden strukturiert (dict → JSON-Zeile) geschrieben. structlog kümmert sich um
Timestamp, Level und JSON-Rendering.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import TracebackType

import structlog
from structlog.typing import FilteringBoundLogger


class SessionLog:
    """Kontext-Manager um einen strukturierten Log-Kanal pro Session."""

    def __init__(self, log_dir: Path) -> None:
        self._log_dir = log_dir
        self._file: object | None = None
        self._logger: FilteringBoundLogger | None = None
        self._path: Path | None = None

    @property
    def path(self) -> Path | None:
        return self._path

    def __enter__(self) -> SessionLog:
        self._log_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        self._path = self._log_dir / f"session_{ts}.jsonl"
        self._file = self._path.open("a", encoding="utf-8")

        structlog.configure(
            processors=[
                structlog.processors.add_log_level,
                structlog.processors.TimeStamper(fmt="iso", utc=True),
                structlog.processors.JSONRenderer(),
            ],
            wrapper_class=structlog.make_filtering_bound_logger(20),  # INFO
            logger_factory=structlog.PrintLoggerFactory(file=self._file),  # type: ignore[arg-type]
            cache_logger_on_first_use=True,
        )
        self._logger = structlog.get_logger("diag-tool.session")
        self._logger.info("session_started", path=str(self._path))
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._logger is not None:
            self._logger.info("session_ended")
        if self._file is not None:
            try:
                self._file.close()  # type: ignore[attr-defined]
            finally:
                self._file = None

    def log(self, event: str, **fields: object) -> None:
        if self._logger is not None:
            self._logger.info(event, **fields)
