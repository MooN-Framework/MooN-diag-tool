"""InjectView — Fault Injection.

Bietet Steuerelemente, um manuell einen ``UdpFrame`` zusammenzustellen und über
den Diagnose-Multicast zu senden. Später erweiterbar um Presets (Drop, Delay,
byzantinisch), sobald Payload-Semantik feststeht.
"""

from __future__ import annotations

from PySide6.QtCore import Signal, Slot
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from diag_tool.net.frame import FrameKind, UdpFrame


class InjectView(QWidget):
    send_requested = Signal(object)  # UdpFrame

    def __init__(self) -> None:
        super().__init__()

        outer = QVBoxLayout(self)
        outer.setContentsMargins(8, 8, 8, 8)

        # ------------------------------------------------------ Manuell
        manual_box = QGroupBox("Manueller Frame")
        form = QFormLayout(manual_box)

        self._kind = QComboBox()
        for k in FrameKind:
            self._kind.addItem(k.name, userData=int(k))
        form.addRow("Kind:", self._kind)

        self._node_id = QSpinBox()
        self._node_id.setRange(0, 255)
        self._node_id.setValue(1)
        form.addRow("Node-ID:", self._node_id)

        self._seq = QSpinBox()
        self._seq.setRange(0, 2**31 - 1)
        form.addRow("Sequenz:", self._seq)

        self._payload = QLineEdit()
        self._payload.setPlaceholderText("Hex, z.B. 'de ad be ef' — leer für kein Payload")
        form.addRow("Payload:", self._payload)

        outer.addWidget(manual_box)

        # ------------------------------------------------------ Aktionen
        actions = QHBoxLayout()
        actions.addStretch(1)
        self._send_btn = QPushButton("Frame senden")
        self._send_btn.clicked.connect(self._on_send)
        actions.addWidget(self._send_btn)
        outer.addLayout(actions)

        # ------------------------------------------------------ Presets (Platzhalter)
        presets_box = QGroupBox("Presets (folgt)")
        presets_layout = QVBoxLayout(presets_box)
        presets_layout.addWidget(
            QPushButton("Drop nächsten Vote von Node…  (TODO)", enabled=False)
        )
        presets_layout.addWidget(
            QPushButton("Delay 100 ms auf Node…  (TODO)", enabled=False)
        )
        presets_layout.addWidget(
            QPushButton("Byzantinisch: divergierender Vote  (TODO)", enabled=False)
        )
        outer.addWidget(presets_box)

        outer.addStretch(1)

    @Slot()
    def _on_send(self) -> None:
        kind = FrameKind(int(self._kind.currentData()))
        node_id = int(self._node_id.value())
        seq = int(self._seq.value())
        payload = _parse_hex(self._payload.text())
        frame = UdpFrame(kind=kind, node_id=node_id, seq=seq, payload=payload)
        self._seq.setValue(seq + 1)
        self.send_requested.emit(frame)


def _parse_hex(text: str) -> bytes:
    cleaned = text.replace(" ", "").replace(",", "").replace("0x", "")
    if not cleaned:
        return b""
    try:
        return bytes.fromhex(cleaned)
    except ValueError:
        return b""
