"""MonitorView — Live-Zustand der drei Nodes + letztes Voting-Ergebnis.

Skaliert: reine Layouts, keine festen Pixel. QSplitter trennt Node-Panel und
Voting-Panel, damit der Nutzer die Aufteilung selbst wählt.
"""

from __future__ import annotations

from datetime import datetime, timezone

from PySide6.QtCore import Qt, Slot
from PySide6.QtWidgets import (
    QGroupBox,
    QHeaderView,
    QLabel,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from diag_tool.core.models import NodeState, NodeStatus, SystemSnapshot
from diag_tool.net.frame import FrameKind, UdpFrame


class MonitorView(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self._snapshot = SystemSnapshot()

        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)

        splitter = QSplitter(Qt.Orientation.Vertical)
        outer.addWidget(splitter, 1)

        # ------------------------------------------------------ Nodes
        nodes_box = QGroupBox("Nodes")
        nodes_layout = QVBoxLayout(nodes_box)

        self._node_table = QTableWidget(0, 4)
        self._node_table.setHorizontalHeaderLabels(
            ["Node-ID", "State", "Seq", "Zuletzt gesehen"]
        )
        header = self._node_table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self._node_table.verticalHeader().setVisible(False)
        self._node_table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._node_table.setSelectionBehavior(
            QTableWidget.SelectionBehavior.SelectRows
        )
        nodes_layout.addWidget(self._node_table)
        splitter.addWidget(nodes_box)

        # ------------------------------------------------------ Voting
        vote_box = QGroupBox("Letztes Voting-Ergebnis")
        vote_layout = QVBoxLayout(vote_box)
        self._vote_label = QLabel("— noch keine Daten —")
        self._vote_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._vote_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        vote_layout.addWidget(self._vote_label, 1)
        splitter.addWidget(vote_box)

        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 1)

    @Slot(object)
    def on_system_frame(self, frame: UdpFrame) -> None:
        """Frame vom Betriebs-Multicast — Snapshot aktualisieren."""
        now = datetime.now(timezone.utc)
        # Heuristische Interpretation, muss beim Wire-Format-Abgleich verfeinert werden.
        state = _state_from_kind(frame.kind)
        self._snapshot.nodes[frame.node_id] = NodeStatus(
            node_id=frame.node_id,
            state=state,
            seq=frame.seq,
            last_seen=now,
        )
        self._snapshot.updated_at = now
        self._refresh_table()

    def _refresh_table(self) -> None:
        nodes = sorted(self._snapshot.nodes.values(), key=lambda n: n.node_id)
        self._node_table.setRowCount(len(nodes))
        for row, node in enumerate(nodes):
            self._node_table.setItem(row, 0, QTableWidgetItem(str(node.node_id)))
            self._node_table.setItem(row, 1, QTableWidgetItem(node.state.value))
            self._node_table.setItem(row, 2, QTableWidgetItem(str(node.seq)))
            self._node_table.setItem(
                row, 3, QTableWidgetItem(node.last_seen.strftime("%H:%M:%S.%f")[:-3])
            )


def _state_from_kind(kind: FrameKind) -> NodeState:
    # Platzhalter-Mapping — solange der Frame-Payload noch nicht dekodiert wird.
    if kind is FrameKind.HEARTBEAT:
        return NodeState.SYNC
    if kind is FrameKind.VOTE:
        return NodeState.VOTING
    if kind is FrameKind.RESULT:
        return NodeState.VOTING
    return NodeState.UNKNOWN
