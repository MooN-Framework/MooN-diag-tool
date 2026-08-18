"""
Dialog to edit the list of hardware nodes.

Each node has: enabled, node_id, host, user, port, password,
remote_binary, remote_config, remote_log, start_cmd, stop_cmd. The
"Test" button in the row runs a quick SSH liveness probe and shows the
result inline.

"Enabled" (checkbox, first column) controls whether a node actually
participates in deploy/sweep actions and in simulated/hardware mode
derivation -- see core.ssh_deploy.enabled_nodes(). A disabled node
stays in this list with all its settings intact, it's just excluded
everywhere else, so you can park a node's config without deleting and
re-typing it later.
"""
from __future__ import annotations

import threading
from typing import Optional

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from ..core.ssh_deploy import HardwareNode, check_reachable


HEADERS = ["Enabled", "ID", "Host", "User", "Port", "Password",
           "Remote binary", "Remote config", "Remote log",
           "Start cmd", "Stop cmd", "Status"]

# Column indices, named so a future header re-ordering only needs
# changes here instead of a search-and-replace over magic numbers.
COL_ENABLED, COL_ID, COL_HOST, COL_USER, COL_PORT, COL_PASSWORD, \
    COL_REMOTE_BINARY, COL_REMOTE_CONFIG, COL_REMOTE_LOG, \
    COL_START_CMD, COL_STOP_CMD, COL_STATUS = range(len(HEADERS))


class HardwareNodesDialog(QDialog):
    _probe_result = Signal(int, bool, str)  # row, ok, message

    def __init__(self, nodes: list[HardwareNode], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Hardware nodes")
        self.resize(1150, 480)
        self._build(nodes)
        self._probe_result.connect(self._on_probe_result)

    def _build(self, nodes: list[HardwareNode]) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 16)
        outer.setSpacing(10)

        hint = QLabel(
            "One row per node. Uses password-based SSH auth via sshpass "
            "(requires the 'sshpass' package on this machine). "
            "Plaintext in this table -- fine for the trivial test-network "
            "password, but don't reuse a real credential here. Untick "
            "'Enabled' to keep a node's config without it taking part in "
            "deploys/sweeps or the simulated/hardware mode switch."
        )
        hint.setProperty("muted", True)
        hint.setWordWrap(True)
        outer.addWidget(hint)

        self.table = QTableWidget(0, len(HEADERS))
        self.table.setHorizontalHeaderLabels(HEADERS)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table.horizontalHeader().setStretchLastSection(False)
        self.table.verticalHeader().setVisible(False)
        # Give rows enough vertical room so the inline editor doesn't
        # squash into an unreadable slit when a cell is double-clicked.
        self.table.verticalHeader().setDefaultSectionSize(34)
        # Reasonable initial column widths — user can still drag them.
        col_widths = {
            COL_ENABLED: 60,
            COL_ID: 50,
            COL_HOST: 150,
            COL_USER: 80,
            COL_PORT: 60,
            COL_PASSWORD: 140,
            COL_REMOTE_BINARY: 180,
            COL_REMOTE_CONFIG: 180,
            COL_REMOTE_LOG: 180,
            COL_START_CMD: 220,
            COL_STOP_CMD: 220,
            COL_STATUS: 160,
        }
        for col, w in col_widths.items():
            self.table.setColumnWidth(col, w)
        outer.addWidget(self.table, 1)

        for n in nodes:
            self._add_row(n)

        toolbar = QHBoxLayout()
        add_btn = QPushButton("+ Add node")
        add_btn.clicked.connect(lambda: self._add_row(HardwareNode(
            node_id=self.table.rowCount(), host="", user="root",
        )))
        del_btn = QPushButton("Remove selected")
        del_btn.setProperty("danger", True)
        del_btn.clicked.connect(self._del_selected)
        probe_btn = QPushButton("Probe all")
        probe_btn.clicked.connect(self._probe_all)
        toolbar.addWidget(add_btn); toolbar.addWidget(del_btn)
        toolbar.addWidget(probe_btn); toolbar.addStretch(1)
        outer.addLayout(toolbar)

        bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        bb.button(QDialogButtonBox.Save).setProperty("accent", True)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        outer.addWidget(bb)

    def _add_row(self, n: HardwareNode) -> None:
        r = self.table.rowCount()
        self.table.insertRow(r)

        enabled_item = QTableWidgetItem()
        enabled_item.setFlags(
            (enabled_item.flags() | Qt.ItemIsUserCheckable) & ~Qt.ItemIsEditable
        )
        enabled_item.setCheckState(Qt.Checked if n.enabled else Qt.Unchecked)
        self.table.setItem(r, COL_ENABLED, enabled_item)

        fields = {
            COL_ID: str(n.node_id),
            COL_HOST: n.host,
            COL_USER: n.user,
            COL_PORT: str(n.port),
            COL_PASSWORD: n.password,
            COL_REMOTE_BINARY: n.remote_binary,
            COL_REMOTE_CONFIG: n.remote_config,
            COL_REMOTE_LOG: n.remote_log,
            COL_START_CMD: n.start_cmd,
            COL_STOP_CMD: n.stop_cmd,
        }
        for col, val in fields.items():
            self.table.setItem(r, col, QTableWidgetItem(val))
        status_item = QTableWidgetItem("—")
        status_item.setFlags(status_item.flags() & ~Qt.ItemIsEditable)
        self.table.setItem(r, COL_STATUS, status_item)

    def _del_selected(self) -> None:
        rows = sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True)
        for r in rows:
            self.table.removeRow(r)

    def collect(self) -> list[HardwareNode]:
        out: list[HardwareNode] = []
        for r in range(self.table.rowCount()):
            try:
                nid = int(self.table.item(r, COL_ID).text())
                port = int(self.table.item(r, COL_PORT).text() or "22")
            except (AttributeError, ValueError):
                continue
            host = self.table.item(r, COL_HOST).text() if self.table.item(r, COL_HOST) else ""
            if not host:
                continue
            enabled_item = self.table.item(r, COL_ENABLED)
            enabled = enabled_item.checkState() == Qt.Checked if enabled_item else True
            out.append(HardwareNode(
                node_id=nid,
                host=host,
                enabled=enabled,
                user=self.table.item(r, COL_USER).text() or "root",
                port=port,
                password=self.table.item(r, COL_PASSWORD).text() if self.table.item(r, COL_PASSWORD) else "",
                remote_binary=self.table.item(r, COL_REMOTE_BINARY).text() or "/opt/voting/node",
                remote_config=self.table.item(r, COL_REMOTE_CONFIG).text() or "/opt/voting/node.toml",
                remote_log=self.table.item(r, COL_REMOTE_LOG).text() or "/opt/voting/node.log",
                start_cmd=self.table.item(r, COL_START_CMD).text() or "nohup {bin} --config {cfg} > {log} 2>&1 < /dev/null & disown",
                stop_cmd=self.table.item(r, COL_STOP_CMD).text() or "pkill -f {bin} || true",
            ))
        return out

    def _probe_all(self) -> None:
        nodes = self.collect()
        for i, n in enumerate(nodes):
            self._set_status(i, "probing…", "muted")

            def worker(row=i, node=n):
                ok, msg = check_reachable(node)
                self._probe_result.emit(row, ok, msg)

            threading.Thread(target=worker, name=f"probe-{n.host}", daemon=True).start()

    @Slot(int, bool, str)
    def _on_probe_result(self, row: int, ok: bool, msg: str) -> None:
        if row >= self.table.rowCount():
            return
        self._set_status(row, "reachable" if ok else f"unreachable: {msg[:60]}",
                         "success" if ok else "danger")

    def _set_status(self, row: int, text: str, kind: str) -> None:
        item = self.table.item(row, COL_STATUS)
        if item is None:
            return
        item.setText(text)
        from PySide6.QtGui import QColor
        colors = {"success": QColor("#23A55A"), "danger": QColor("#DA373C"),
                  "muted": QColor("#949BA4")}
        item.setForeground(colors.get(kind, QColor("#DBDEE1")))
