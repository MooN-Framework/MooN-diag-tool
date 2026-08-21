"""
Status tab: compact table of discovered nodes plus a detail panel.

- Nodes not seen for more than LOST_AFTER_S seconds are labelled "Lost"
  (dim colour); if they reappear, the real state comes back with them.
- The "Poll all" button fetches live get_status answers from every known
  node (worker thread so the UI does not freeze).
- Selecting a row shows the last diag status as JSON at the bottom.
- Every render pass resets the state cell's colour explicitly so a
  transition Failsafe -> normal or a re-populated row (after Clear
  registry) can't leave stale red text behind.
"""
from __future__ import annotations

import json
import threading
import time
from typing import Optional

from PySide6.QtCore import Qt, QTimer, Signal, Slot
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..core.node_registry import NodeRegistry
from ..core.ssh_deploy import get_os_uptime, nodes_from_json


HEADERS = [
    "ID", "State", "Seq", "Session", "Frames", "Last seen",
    "Process uptime", "OS uptime", "Peers (diag)",
]
LOST_AFTER_S = 10.0


def _fmt_duration(seconds: float) -> str:
    if seconds < 0:
        return "—"
    seconds = int(seconds)
    d, rem = divmod(seconds, 86400)
    h, rem = divmod(rem, 3600)
    m, s = divmod(rem, 60)
    if d:
        return f"{d}d {h}h {m}m"
    if h:
        return f"{h}h {m}m"
    return f"{m}m {s}s"

# Colours (kept in sync with ui.style)
DEFAULT_TEXT = QColor("#DBDEE1")
MUTED_TEXT   = QColor("#6D7076")
STATE_COLORS: dict[str, QColor] = {
    "Failsafe":        QColor("#DA373C"),
    "Isolation":       QColor("#F0B232"),
    "ErrorManagement": QColor("#F0B232"),
    "Startup":         QColor("#5865F2"),
    "InitSync":        QColor("#5865F2"),
    "Lost":            MUTED_TEXT,
}


class StatusTab(QWidget):
    _poll_done = Signal()
    _uptime_done = Signal()

    def __init__(self, registry: NodeRegistry, diag_client_provider, settings_provider, parent=None) -> None:
        super().__init__(parent)
        self.registry = registry
        self._get_diag = diag_client_provider
        self._get_settings = settings_provider
        # node_id -> (os_uptime_seconds_at_fetch, local_monotonic_fetch_time)
        self._os_uptime: dict[int, tuple[float, float]] = {}
        self._os_uptime_lock = threading.Lock()

        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 20, 20, 20)
        outer.setSpacing(14)

        head = QHBoxLayout()
        title = QLabel("Discovered nodes")
        title.setProperty("heading", True)
        self.count_lbl = QLabel("0")
        self.count_lbl.setProperty("muted", True)
        head.addWidget(title); head.addWidget(self.count_lbl); head.addStretch(1)
        self.poll_btn = QPushButton("Poll all")
        self.poll_btn.setProperty("accent", True)
        self.poll_btn.clicked.connect(self._on_poll_all)
        self.uptime_btn = QPushButton("Refresh OS uptime")
        self.uptime_btn.setToolTip(
            "SSHes into every discovered node that matches a configured, "
            "enabled hardware node and reads /proc/uptime. Simulated-mode "
            "or unconfigured nodes show \"—\" since there's nothing to SSH "
            "into."
        )
        self.uptime_btn.clicked.connect(self._on_refresh_os_uptime)
        self.clear_btn = QPushButton("Clear registry")
        self.clear_btn.setProperty("danger", True)
        self.clear_btn.clicked.connect(self._on_clear)
        head.addWidget(self.poll_btn); head.addWidget(self.uptime_btn); head.addWidget(self.clear_btn)
        outer.addLayout(head)

        split = QSplitter(Qt.Vertical)
        split.setChildrenCollapsible(False)

        self.table = QTableWidget(0, len(HEADERS))
        self.table.setHorizontalHeaderLabels(HEADERS)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.itemSelectionChanged.connect(self._on_selection)
        split.addWidget(self.table)

        detail_wrap = QWidget()
        dl = QVBoxLayout(detail_wrap)
        dl.setContentsMargins(0, 8, 0, 0); dl.setSpacing(6)
        dt = QLabel("Details (last get_status)")
        dt.setProperty("heading", True)
        dl.addWidget(dt)
        self.detail = QPlainTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setPlaceholderText("Select a row to see the JSON details.")
        mono = QFont("JetBrains Mono, Menlo, Consolas, monospace", 9)
        mono.setStyleHint(QFont.Monospace)
        self.detail.setFont(mono)
        dl.addWidget(self.detail)
        split.addWidget(detail_wrap)

        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        outer.addWidget(split)

        # Base font for cell text — used to reset per render so we can
        # toggle bold on critical states.
        self._cell_font = self.table.font()

        self._timer = QTimer(self)
        self._timer.setInterval(300)
        self._timer.timeout.connect(self._refresh)
        self._timer.start()

        self._poll_done.connect(self._on_poll_done)
        self._uptime_done.connect(self._on_uptime_done)

    def _on_clear(self) -> None:
        self.registry.clear()
        self.table.setRowCount(0)
        self.detail.clear()

    def _on_poll_all(self) -> None:
        diag = self._get_diag()
        if diag is None:
            self.detail.setPlainText("[Error] diag client not connected.")
            return
        ids = self.registry.ids()
        if not ids:
            self.detail.setPlainText("No nodes discovered yet — nothing to poll.")
            return
        self.poll_btn.setEnabled(False)
        self.poll_btn.setText("Polling…")

        def worker() -> None:
            try:
                for nid in ids:
                    status = diag.get_status(nid, timeout=1.5)
                    if status is not None:
                        self.registry.observe_diag_status(nid, status)
            finally:
                self._poll_done.emit()

        threading.Thread(target=worker, name="status-poll", daemon=True).start()

    @Slot()
    def _on_poll_done(self) -> None:
        self.poll_btn.setEnabled(True)
        self.poll_btn.setText("Poll all")

    def _on_refresh_os_uptime(self) -> None:
        s = self._get_settings()
        hw_by_id = {n.node_id: n for n in nodes_from_json(s.hardware_nodes_json) if n.enabled}
        ids = self.registry.ids()
        targets = [(nid, hw_by_id[nid]) for nid in ids if nid in hw_by_id]
        if not targets:
            self.detail.setPlainText(
                "No discovered node matches a configured + enabled hardware "
                "node (Package tab → \"Configure hardware nodes…\") -- "
                "nothing to query over SSH. Simulated-mode nodes have no "
                "OS to ask."
            )
            return
        self.uptime_btn.setEnabled(False)
        self.uptime_btn.setText("Querying…")

        def worker() -> None:
            try:
                for nid, hn in targets:
                    secs = get_os_uptime(hn)
                    if secs is not None:
                        with self._os_uptime_lock:
                            self._os_uptime[nid] = (secs, time.monotonic())
            finally:
                self._uptime_done.emit()

        threading.Thread(target=worker, name="status-os-uptime", daemon=True).start()

    @Slot()
    def _on_uptime_done(self) -> None:
        self.uptime_btn.setEnabled(True)
        self.uptime_btn.setText("Refresh OS uptime")

    def _on_selection(self) -> None:
        row = self.table.currentRow()
        if row < 0:
            return
        item = self.table.item(row, 0)
        if item is None:
            return
        nid = int(item.text())
        v = self.registry.get(nid)
        if v is None or v.diag_status is None:
            self.detail.setPlainText(
                f"No diag status cached for node {nid}.\n"
                f"Click 'Poll all' above."
            )
            return
        self.detail.setPlainText(json.dumps(v.diag_status, indent=2))

    def _refresh(self) -> None:
        views = self.registry.snapshot()
        now = time.monotonic()

        # Count online (non-Lost) separately from total.
        n_lost = sum(1 for v in views if now - v.last_seen > LOST_AFTER_S)
        n_online = len(views) - n_lost
        if n_lost:
            self.count_lbl.setText(f"{n_online} online · {n_lost} lost")
        else:
            self.count_lbl.setText(f"{n_online} online")

        self.table.setRowCount(len(views))
        for row, v in enumerate(views):
            is_lost = now - v.last_seen > LOST_AFTER_S
            state_display = "Lost" if is_lost else v.last_state

            peers = ""
            if v.diag_status and "peers" in v.diag_status:
                peers = " · ".join(
                    f"{p['id']}:{p.get('health','?')}" for p in v.diag_status["peers"]
                )

            proc_uptime = (
                "—" if v.session_started_at == 0.0
                else _fmt_duration(now - v.session_started_at)
            )

            with self._os_uptime_lock:
                os_entry = self._os_uptime.get(v.node_id)
            if os_entry is None:
                os_uptime = "—"
            else:
                base_secs, fetched_at = os_entry
                live_estimate = base_secs + (now - fetched_at)
                os_uptime = f"{_fmt_duration(live_estimate)} (synced {int(now - fetched_at)}s ago)"

            cells = [
                str(v.node_id),
                state_display,
                str(v.last_seq),
                str(v.last_session),
                str(v.frame_count),
                f"{now - v.last_seen:.1f} s",
                proc_uptime,
                os_uptime,
                peers,
            ]

            row_color = MUTED_TEXT if is_lost else DEFAULT_TEXT
            state_color = STATE_COLORS.get(state_display, row_color)
            state_is_critical = state_display in STATE_COLORS

            for col, txt in enumerate(cells):
                item = self.table.item(row, col)
                if item is None:
                    item = QTableWidgetItem(txt)
                    self.table.setItem(row, col, item)
                else:
                    item.setText(txt)

                # ALWAYS reset colour and font — this is the fix for
                # stale colours when a node changes state or when the
                # row is reused after Clear registry.
                if col == 1:
                    item.setForeground(state_color)
                    f = QFont(self._cell_font)
                    f.setBold(state_is_critical)
                    item.setFont(f)
                else:
                    item.setForeground(row_color)
                    item.setFont(self._cell_font)

        self.table.resizeColumnsToContents()
