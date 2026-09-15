"""
Dialog to edit the list of hardware nodes.

Each node has: enabled, node_id, host, user, port, password,
remote_binary, remote_config, remote_log_dir, start_cmd, stop_cmd. The
"Test" button in the row runs a quick SSH liveness probe and shows the
result inline.

remote_log_dir is THE one log location to configure: passed to the
node binary as `--log-dir` (the framework's file-logging support for
HW deployment), it maintains per-session log files plus a stable
"current" symlink itself -- see HardwareNode.current_log_path(). The
raw stdout/stderr catch-all is derived from it automatically (not
separately configurable), so there's only this one field to set.

"Enabled" (checkbox, first column) controls whether a node actually
participates in deploy/sweep actions and in simulated/hardware mode
derivation -- see core.ssh_deploy.enabled_nodes(). A disabled node
stays in this list with all its settings intact, it's just excluded
everywhere else, so you can park a node's config without deleting and
re-typing it later.
"""
from __future__ import annotations

import threading

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
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
           "Remote binary", "Remote config", "Remote log dir",
           "Start cmd", "Stop cmd", "Status"]

# Column indices, named so a future header re-ordering only needs
# changes here instead of a search-and-replace over magic numbers.
COL_ENABLED, COL_ID, COL_HOST, COL_USER, COL_PORT, COL_PASSWORD, \
    COL_REMOTE_BINARY, COL_REMOTE_CONFIG, COL_REMOTE_LOG_DIR, \
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
            COL_REMOTE_LOG_DIR: 180,
            COL_START_CMD: 260,
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
        dup_btn = QPushButton("Duplicate selected")
        dup_btn.setToolTip(
            "Copy the selected row(s) as new rows with every field the "
            "same except the ID (auto-assigned to the next free one) -- "
            "handy when a new node is identical except for its IP."
        )
        dup_btn.clicked.connect(self._duplicate_selected)
        del_btn = QPushButton("Remove selected")
        del_btn.setProperty("danger", True)
        del_btn.clicked.connect(self._del_selected)
        probe_btn = QPushButton("Probe all")
        probe_btn.clicked.connect(self._probe_all)
        toolbar.addWidget(add_btn); toolbar.addWidget(dup_btn); toolbar.addWidget(del_btn)
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
            COL_REMOTE_LOG_DIR: n.remote_log_dir,
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

    def _row_cell_text(self, row: int, col: int) -> str:
        item = self.table.item(row, col)
        return item.text() if item else ""

    def _duplicate_selected(self) -> None:
        """Clone the selected row(s) verbatim (same host, credentials,
        commands, everything) except the ID, which is auto-assigned to
        the next free one -- keeping the source row's ID would create
        two rows claiming the same node. The common case this is for:
        a new node that's identical to an existing one apart from its
        IP -- duplicate, then just edit the Host cell on the copy."""
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        if not rows:
            QMessageBox.information(
                self, "Nothing selected",
                "Select one or more rows first, then Duplicate selected.",
            )
            return

        next_id = 0
        for r in range(self.table.rowCount()):
            try:
                next_id = max(next_id, int(self._row_cell_text(r, COL_ID)) + 1)
            except ValueError:
                pass

        new_rows: list[int] = []
        for r in rows:
            enabled_item = self.table.item(r, COL_ENABLED)
            enabled = enabled_item.checkState() == Qt.Checked if enabled_item else True
            try:
                port = int(self._row_cell_text(r, COL_PORT) or "22")
            except ValueError:
                port = 22
            node = HardwareNode(
                node_id=next_id,
                host=self._row_cell_text(r, COL_HOST),
                enabled=enabled,
                user=self._row_cell_text(r, COL_USER) or "root",
                port=port,
                password=self._row_cell_text(r, COL_PASSWORD),
                remote_binary=self._row_cell_text(r, COL_REMOTE_BINARY) or "/opt/voting/node",
                remote_config=self._row_cell_text(r, COL_REMOTE_CONFIG) or "/opt/voting/node.toml",
                remote_log_dir=self._row_cell_text(r, COL_REMOTE_LOG_DIR) or "/opt/voting/logs",
                start_cmd=self._row_cell_text(r, COL_START_CMD)
                or "nohup {bin} --config {cfg} --log-dir {log_dir} > {log} 2>&1 < /dev/null & disown",
                stop_cmd=self._row_cell_text(r, COL_STOP_CMD) or "pkill -f {bin} || true",
            )
            self._add_row(node)
            new_rows.append(self.table.rowCount() - 1)
            next_id += 1

        # Select the new row(s) and jump straight to the Host cell of
        # the first one -- the one field duplicating this way is
        # actually meant to leave different.
        self.table.clearSelection()
        for r in new_rows:
            self.table.selectRow(r)
        self.table.setCurrentCell(new_rows[0], COL_HOST)
        self.table.scrollToItem(self.table.item(new_rows[0], COL_HOST))

    def _collect_with_rows(self) -> list[tuple[int, HardwareNode]]:
        """Same filtering as collect(), but keeps each node's actual
        table row alongside it. collect() alone loses that mapping
        whenever a row is skipped (invalid id/port, empty host) --
        callers that need to write something back to a specific row
        (e.g. _probe_all's status column) must use this instead, or
        every row after the first skipped one gets the wrong result."""
        out: list[tuple[int, HardwareNode]] = []
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
            out.append((r, HardwareNode(
                node_id=nid,
                host=host,
                enabled=enabled,
                user=self.table.item(r, COL_USER).text() or "root",
                port=port,
                password=self.table.item(r, COL_PASSWORD).text() if self.table.item(r, COL_PASSWORD) else "",
                remote_binary=self.table.item(r, COL_REMOTE_BINARY).text() or "/opt/voting/node",
                remote_config=self.table.item(r, COL_REMOTE_CONFIG).text() or "/opt/voting/node.toml",
                remote_log_dir=self.table.item(r, COL_REMOTE_LOG_DIR).text() or "/opt/voting/logs",
                start_cmd=self.table.item(r, COL_START_CMD).text() or "nohup {bin} --config {cfg} --log-dir {log_dir} > {log} 2>&1 < /dev/null & disown",
                stop_cmd=self.table.item(r, COL_STOP_CMD).text() or "pkill -f {bin} || true",
            )))
        return out

    def collect(self) -> list[HardwareNode]:
        return [n for _row, n in self._collect_with_rows()]

    def _probe_all(self) -> None:
        # BUG (fixed): this used to enumerate() self.collect()'s
        # filtered list and use that 0..N index as the table row to
        # write "probing…"/"reachable"/... into. collect() silently
        # skips rows with an empty host or a non-numeric id/port, so
        # as soon as any row before the end was skipped, every
        # subsequent probe result landed on the wrong table row.
        for row, n in self._collect_with_rows():
            self._set_status(row, "probing…", "muted")

            def worker(row=row, node=n):
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
