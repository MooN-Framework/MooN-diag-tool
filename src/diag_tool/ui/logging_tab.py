"""
Log tab: live feed of every received telegram.

Layout:
  Top:    compact toolbar (filters, session toggle, actions)
  Bottom: table with the last N messages
"""
from __future__ import annotations

import time
from collections import deque
from typing import Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..core.diag_client import DiagTelegram
from ..core.os_open import OpenError, open_path
from ..core.session_logger import SessionLogger
from ..core.wire_decoder import DecodedFrame


HEADERS = ["Time (s)", "Channel", "Node", "Type/State", "Summary"]


class LoggingTab(QWidget):
    def __init__(self, session_logger: SessionLogger, parent=None) -> None:
        super().__init__(parent)
        self.session = session_logger
        self._buf: deque = deque(maxlen=5000)
        self._t0 = time.monotonic()

        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 20, 20, 20)
        outer.setSpacing(12)

        # Header + counter
        head = QHBoxLayout()
        title = QLabel("Live feed"); title.setProperty("heading", True)
        self.stats_lbl = QLabel("0 / 0")
        self.stats_lbl.setProperty("muted", True)
        head.addWidget(title); head.addWidget(self.stats_lbl); head.addStretch(1)
        self.open_logs_btn = QPushButton("Open logs folder")
        self.open_logs_btn.clicked.connect(self._on_open_logs_folder)
        head.addWidget(self.open_logs_btn)
        self.clear_btn = QPushButton("Clear buffer")
        self.clear_btn.setProperty("danger", True)
        self.clear_btn.clicked.connect(self._on_clear)
        head.addWidget(self.clear_btn)
        outer.addLayout(head)

        # Filter row
        filt = QHBoxLayout(); filt.setSpacing(10)
        self.chan_filter = QComboBox()
        self.chan_filter.addItems(["All channels", "op", "diag"])
        self.chan_filter.setFixedWidth(140)
        self.node_filter = QLineEdit()
        self.node_filter.setPlaceholderText("Filter by node id…")
        self.node_filter.setFixedWidth(160)
        self.text_filter = QLineEdit()
        self.text_filter.setPlaceholderText("Full-text search…")
        self.cap_spin = QSpinBox()
        self.cap_spin.setRange(100, 100_000)
        self.cap_spin.setValue(5000)
        self.cap_spin.setSuffix(" buffer")
        self.cap_spin.setFixedWidth(120)
        self.cap_spin.valueChanged.connect(self._on_cap_changed)
        filt.addWidget(self.chan_filter)
        filt.addWidget(self.node_filter)
        filt.addWidget(self.text_filter, 1)
        filt.addWidget(self.cap_spin)
        outer.addLayout(filt)

        # Session toggle
        sess = QHBoxLayout()
        self.session_toggle = QCheckBox("Write to file (JSONL)")
        self.session_toggle.stateChanged.connect(self._on_session_toggle)
        self.session_path_lbl = QLabel("— no active session —")
        self.session_path_lbl.setProperty("muted", True)
        sess.addWidget(self.session_toggle)
        sess.addWidget(self.session_path_lbl, 1)
        outer.addLayout(sess)

        # Table
        self.table = QTableWidget(0, len(HEADERS))
        self.table.setHorizontalHeaderLabels(HEADERS)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        mono = QFont("JetBrains Mono, Menlo, Consolas, monospace", 9)
        mono.setStyleHint(QFont.Monospace)
        self.table.setFont(mono)
        outer.addWidget(self.table, 1)

        self._timer = QTimer(self)
        self._timer.setInterval(200)
        self._timer.timeout.connect(self._render)
        self._timer.start()

    # Ingest from Bus signals (UI thread -- safe)
    def on_op_frame(self, frame: DecodedFrame, ts_mono: float) -> None:
        self._buf.append(("op", ts_mono, frame))

    def on_diag_telegram(self, tel: DiagTelegram) -> None:
        self._buf.append(("diag", tel.ts_mono, tel))

    def _on_session_toggle(self, state) -> None:
        if state == Qt.Checked.value:
            path = self.session.start()
            self.session_path_lbl.setText(f"writing → {path}")
        else:
            self.session.stop()
            self.session_path_lbl.setText("— no active session —")

    def _on_cap_changed(self, v: int) -> None:
        self._buf = deque(self._buf, maxlen=v)

    def _on_clear(self) -> None:
        self._buf.clear()
        self.table.setRowCount(0)

    def _on_open_logs_folder(self) -> None:
        try:
            self.session.base_dir.mkdir(parents=True, exist_ok=True)
            open_path(self.session.base_dir)
        except OpenError as e:
            QMessageBox.warning(self, "Could not open folder", str(e))

    def _render(self) -> None:
        chan = self.chan_filter.currentText()
        node_txt = self.node_filter.text().strip()
        node_id: Optional[int] = None
        if node_txt:
            try:
                node_id = int(node_txt)
            except ValueError:
                pass
        needle = self.text_filter.text().strip().lower()

        rows = []
        for ch, ts, obj in self._buf:
            if chan not in ("All channels", ch):
                continue
            if node_id is not None:
                nid = obj.node_id if ch == "op" else (obj.source_node_id or -1)
                if nid != node_id:
                    continue
            summary = obj.summary() if ch == "op" else str(obj.raw)
            if needle and needle not in summary.lower():
                continue
            rows.append((ts, ch, obj, summary))

        self.table.setRowCount(len(rows))
        for row, (ts, ch, obj, summary) in enumerate(rows):
            if ch == "op":
                cells = [
                    f"{ts - self._t0:.3f}",
                    "op",
                    str(obj.node_id),
                    f"{obj.discriminator_name}/{obj.node_state_name}",
                    summary,
                ]
                bad = (not obj.crc_ok) or (obj.error is not None)
            else:
                nid = obj.source_node_id
                cells = [
                    f"{ts - self._t0:.3f}",
                    "diag",
                    "—" if nid is None else str(nid),
                    obj.kind,
                    summary,
                ]
                bad = obj.kind == "error"

            for col, txt in enumerate(cells):
                item = self.table.item(row, col)
                if item is None:
                    item = QTableWidgetItem(txt)
                    self.table.setItem(row, col, item)
                else:
                    item.setText(txt)
                if bad:
                    item.setForeground(QColor("#DA373C"))
                elif ch == "diag":
                    item.setForeground(QColor("#5A96F7"))
                else:
                    item.setForeground(QColor("#DBDEE1"))

        self.stats_lbl.setText(f"{len(rows)} shown · {len(self._buf)} total")

        # Auto-scroll when the user is already at the bottom
        vsb = self.table.verticalScrollBar()
        if vsb.value() > vsb.maximum() - 30:
            self.table.scrollToBottom()
