"""
Dialog to edit the list of hardware nodes.

Each node has: node_id, host, user, port, password, remote_binary,
remote_config, start_cmd, stop_cmd. The "Test" button in the row runs a
quick SSH liveness probe and shows the result inline.
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


HEADERS = ["ID", "Host", "User", "Port", "Password",
           "Remote binary", "Remote config",
           "Start cmd", "Stop cmd", "Status"]


class HardwareNodesDialog(QDialog):
    _probe_result = Signal(int, bool, str)  # row, ok, message

    def __init__(self, nodes: list[HardwareNode], parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Hardware nodes")
        self.resize(1100, 480)
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
            "password, but don't reuse a real credential here."
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
            0: 50,   # ID
            1: 150,  # Host
            2: 80,   # User
            3: 60,   # Port
            4: 140,  # Password
            5: 180,  # Remote binary
            6: 180,  # Remote config
            7: 220,  # Start cmd
            8: 220,  # Stop cmd
            9: 160,  # Status
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
        fields = [
            str(n.node_id), n.host, n.user, str(n.port), n.password,
            n.remote_binary, n.remote_config, n.start_cmd, n.stop_cmd,
        ]
        for col, val in enumerate(fields):
            self.table.setItem(r, col, QTableWidgetItem(val))
        status_item = QTableWidgetItem("—")
        status_item.setFlags(status_item.flags() & ~Qt.ItemIsEditable)
        self.table.setItem(r, 9, status_item)

    def _del_selected(self) -> None:
        rows = sorted({i.row() for i in self.table.selectedIndexes()}, reverse=True)
        for r in rows:
            self.table.removeRow(r)

    def collect(self) -> list[HardwareNode]:
        out: list[HardwareNode] = []
        for r in range(self.table.rowCount()):
            try:
                nid = int(self.table.item(r, 0).text())
                port = int(self.table.item(r, 3).text() or "22")
            except (AttributeError, ValueError):
                continue
            host = self.table.item(r, 1).text() if self.table.item(r, 1) else ""
            if not host:
                continue
            out.append(HardwareNode(
                node_id=nid,
                host=host,
                user=self.table.item(r, 2).text() or "root",
                port=port,
                password=self.table.item(r, 4).text() if self.table.item(r, 4) else "",
                remote_binary=self.table.item(r, 5).text() or "/opt/voting/node",
                remote_config=self.table.item(r, 6).text() or "/opt/voting/node.toml",
                start_cmd=self.table.item(r, 7).text() or "systemctl restart voting-node",
                stop_cmd=self.table.item(r, 8).text() or "systemctl stop voting-node || true",
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
        item = self.table.item(row, 9)
        if item is None:
            return
        item.setText(text)
        from PySide6.QtGui import QColor
        colors = {"success": QColor("#23A55A"), "danger": QColor("#DA373C"),
                  "muted": QColor("#949BA4")}
        item.setForeground(colors.get(kind, QColor("#DBDEE1")))
