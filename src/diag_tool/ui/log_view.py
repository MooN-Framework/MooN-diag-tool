"""LogView — chronologische Anzeige empfangener/gesendeter Frames.

Puffer ist begrenzt (Ring), damit lange Sessions nicht den Speicher fluten.
"""

from __future__ import annotations

from collections import deque
from datetime import datetime

from PySide6.QtCore import Slot
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QHBoxLayout,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from diag_tool.net.frame import UdpFrame

_MAX_LINES = 5000


class LogView(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self._lines: deque[str] = deque(maxlen=_MAX_LINES)

        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)

        controls = QHBoxLayout()
        self._auto_scroll = QCheckBox("Auto-Scroll")
        self._auto_scroll.setChecked(True)
        controls.addWidget(self._auto_scroll)
        controls.addStretch(1)
        clear_btn = QPushButton("Leeren")
        clear_btn.clicked.connect(self._clear)
        controls.addWidget(clear_btn)
        outer.addLayout(controls)

        self._text = QPlainTextEdit()
        self._text.setReadOnly(True)
        mono = QFont("monospace")
        mono.setStyleHint(QFont.StyleHint.Monospace)
        self._text.setFont(mono)
        outer.addWidget(self._text, 1)

    @Slot(object)
    def append_rx_frame(self, frame: UdpFrame) -> None:
        self._append(_fmt(frame, direction="RX"))

    @Slot(object)
    def append_tx_frame(self, frame: UdpFrame) -> None:
        self._append(_fmt(frame, direction="TX"))

    def _append(self, line: str) -> None:
        self._lines.append(line)
        self._text.appendPlainText(line)
        if self._auto_scroll.isChecked():
            self._text.verticalScrollBar().setValue(
                self._text.verticalScrollBar().maximum()
            )

    @Slot()
    def _clear(self) -> None:
        self._lines.clear()
        self._text.clear()


def _fmt(frame: UdpFrame, *, direction: str) -> str:
    ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
    return (
        f"{ts} {direction} kind={frame.kind.name:9s} "
        f"node={frame.node_id:3d} seq={frame.seq:10d} "
        f"len={len(frame.payload):4d}"
    )
