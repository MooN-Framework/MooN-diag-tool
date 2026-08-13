"""
Inject tab.

Layout:
  Top:    Targets + command / preset side by side
  Middle: Parameter form + action panel (send, broadcast)
  Bottom: Response log (splitter, resizable)

Thread-safety: the send worker never touches widgets directly. It emits
`_log_signal` and `_done_signal`, which are handled on the UI thread.
The previous version touched widgets from the worker and that caused
the wl_display crash when clicking "Send".
"""
from __future__ import annotations

import json
import threading
from typing import Any, Optional

from PySide6.QtCore import Qt, QTimer, Signal, Slot
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from ..core.diag_client import COMMAND_SCHEMAS
from ..core.node_registry import NodeRegistry


PRESETS: list[dict[str, Any]] = [
    {"label": "T01 · silent (drop_results + drop_acks)",
     "compound": [("inject_drop_results", {"count": 1}),
                  ("inject_drop_acks", {"count": 1})]},
    {"label": "T02 · shutdown", "cmd": "inject_shutdown", "params": {}},
    {"label": "T03 · drop_inputs", "cmd": "inject_drop_inputs", "params": {"count": 1}},
    {"label": "T04 · drop_cyclesync", "cmd": "inject_drop_cyclesync", "params": {"count": 1}},
    {"label": "T05 · targeted_input (speed=999)",
     "cmd": "inject_targeted_input", "params": {"value": {"speed": 999}}},
    {"label": "T06 · corrupt_result", "cmd": "inject_corrupt_result", "params": {"count": 1}},
    {"label": "T07 · fake_crc", "cmd": "inject_fake_crc", "params": {"count": 1}},
    {"label": "T08 · cycle_delay 100 ms", "cmd": "inject_cycle_delay",
     "params": {"ms": 100, "count": 1}},
    {"label": "T10 · drop_from_peer (bit 0 = node 0)",
     "cmd": "inject_drop_from_peer", "params": {"peers_mask": 0b0000_0001}},
    {"label": "T15 · divergent_publisher", "cmd": "inject_divergent_publisher",
     "params": {"count": 1}},
    {"label": "T16 · fake_phase_header (Isolation)",
     "cmd": "inject_fake_phase_header", "params": {"count": 1, "wire_value": 0x09}},
    {"label": "Clear · lift all injections",
     "cmd": "clear_injection", "params": {}},
]


class InjectTab(QWidget):
    _log_signal = Signal(str)
    _done_signal = Signal()

    def __init__(self, registry: NodeRegistry, diag_client_provider, parent=None) -> None:
        super().__init__(parent)
        self.registry = registry
        self._get_diag = diag_client_provider
        self._param_widgets: dict[str, QWidget] = {}

        self._build()

        self._log_signal.connect(self._append_log)
        self._done_signal.connect(self._on_worker_done)

        self._timer = QTimer(self)
        self._timer.setInterval(500)
        self._timer.timeout.connect(self._refresh_targets)
        self._timer.start()

        self._rebuild_params(self.cmd_combo.currentText())

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 20, 20, 20)
        outer.setSpacing(16)

        split = QSplitter(Qt.Vertical)
        split.setChildrenCollapsible(False)

        # === Top: setup
        top = QWidget()
        top_l = QVBoxLayout(top)
        top_l.setContentsMargins(0, 0, 0, 0)
        top_l.setSpacing(14)

        row1 = QHBoxLayout(); row1.setSpacing(14)

        # Targets — wrapped in a GroupBox so it aligns visually with
        # the Command box on the right (same border, same title bar,
        # same top edge).
        targets_box = QGroupBox("Targets")
        tb = QVBoxLayout(targets_box)
        tb.setContentsMargins(12, 18, 12, 12); tb.setSpacing(6)
        self.targets_hint = QLabel("Multi-select · from discovered nodes")
        self.targets_hint.setProperty("muted", True)
        tb.addWidget(self.targets_hint)
        self.targets_list = QListWidget()
        self.targets_list.setSelectionMode(QAbstractItemView.MultiSelection)
        self.targets_list.setMinimumHeight(140)
        tb.addWidget(self.targets_list)
        row1.addWidget(targets_box, 1)

        # Command + Preset — same GroupBox structure.
        command_box = QGroupBox("Command")
        cb = QVBoxLayout(command_box)
        cb.setContentsMargins(12, 18, 12, 12); cb.setSpacing(6)
        self.cmd_combo = QComboBox()
        for cmd in sorted(COMMAND_SCHEMAS.keys()):
            self.cmd_combo.addItem(cmd)
        self.cmd_combo.addItem("get_status")
        self.cmd_combo.currentTextChanged.connect(self._rebuild_params)
        cb.addWidget(self.cmd_combo)

        preset_lbl = QLabel("or load a preset")
        preset_lbl.setProperty("muted", True)
        cb.addWidget(preset_lbl)
        self.preset_combo = QComboBox()
        self.preset_combo.addItem("— choose preset —", None)
        for i, p in enumerate(PRESETS):
            self.preset_combo.addItem(p["label"], i)
        self.preset_combo.currentIndexChanged.connect(self._on_preset)
        cb.addWidget(self.preset_combo)
        cb.addStretch(1)
        row1.addWidget(command_box, 1)

        top_l.addLayout(row1)

        # Row 2: parameters + actions
        row2 = QHBoxLayout(); row2.setSpacing(14)

        params_box = QGroupBox("Parameters")
        pl = QVBoxLayout(params_box)
        pl.setContentsMargins(12, 18, 12, 12)
        self.params_form_holder = QWidget()
        self.params_form = QFormLayout(self.params_form_holder)
        self.params_form.setContentsMargins(0, 0, 0, 0)
        self.params_form.setLabelAlignment(Qt.AlignRight)
        pl.addWidget(self.params_form_holder)
        self.no_params_lbl = QLabel("This command takes no parameters.")
        self.no_params_lbl.setProperty("muted", True)
        pl.addWidget(self.no_params_lbl)
        pl.addStretch(1)
        row2.addWidget(params_box, 2)

        actions_box = QGroupBox("Action")
        al = QVBoxLayout(actions_box)
        al.setContentsMargins(12, 18, 12, 12); al.setSpacing(10)
        self.send_btn = QPushButton("Send command")
        self.send_btn.setProperty("accent", True)
        self.send_btn.clicked.connect(self._on_send)
        al.addWidget(self.send_btn)

        ack_hint = QLabel("Waits up to 3 s per target for a Staged ack.")
        ack_hint.setProperty("muted", True); ack_hint.setWordWrap(True)
        al.addWidget(ack_hint)

        al.addSpacing(8)
        bi_lbl = QLabel("Broadcast input (to all nodes)")
        bi_lbl.setProperty("muted", True)
        al.addWidget(bi_lbl)
        self.broadcast_input = QLineEdit('{"speed": 100}')
        al.addWidget(self.broadcast_input)
        self.broadcast_btn = QPushButton("Broadcast input")
        self.broadcast_btn.clicked.connect(self._on_broadcast)
        al.addWidget(self.broadcast_btn)
        al.addStretch(1)
        row2.addWidget(actions_box, 1)
        top_l.addLayout(row2)
        top_l.addStretch(1)
        split.addWidget(top)

        # === Bottom: response log
        bottom = QWidget()
        bl = QVBoxLayout(bottom)
        bl.setContentsMargins(0, 0, 0, 0); bl.setSpacing(6)
        head = QHBoxLayout()
        h_lbl = QLabel("Response log"); h_lbl.setProperty("heading", True)
        head.addWidget(h_lbl); head.addStretch(1)
        clr_btn = QPushButton("Clear")
        clr_btn.setProperty("danger", True)
        clr_btn.clicked.connect(lambda: self.response_view.clear())
        head.addWidget(clr_btn)
        bl.addLayout(head)
        self.response_view = QPlainTextEdit()
        self.response_view.setReadOnly(True)
        self.response_view.setPlaceholderText("Responses from nodes will appear here.")
        mono = QFont("JetBrains Mono, Menlo, Consolas, monospace", 9)
        mono.setStyleHint(QFont.Monospace)
        self.response_view.setFont(mono)
        bl.addWidget(self.response_view)
        split.addWidget(bottom)

        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        outer.addWidget(split)

    def _refresh_targets(self) -> None:
        current = {self.targets_list.item(i).data(Qt.UserRole)
                   for i in range(self.targets_list.count())
                   if self.targets_list.item(i).isSelected()}
        ids = self.registry.ids()
        self.targets_list.blockSignals(True)
        self.targets_list.clear()
        for nid in ids:
            it = QListWidgetItem(f"Node {nid}")
            it.setData(Qt.UserRole, nid)
            self.targets_list.addItem(it)
            if nid in current:
                it.setSelected(True)
        self.targets_list.blockSignals(False)
        if not ids:
            self.targets_hint.setText("No nodes discovered yet")
        else:
            self.targets_hint.setText(f"{len(ids)} discovered · multi-select supported")

    def _selected_targets(self) -> list[int]:
        out = []
        for i in range(self.targets_list.count()):
            it = self.targets_list.item(i)
            if it.isSelected():
                data = it.data(Qt.UserRole)
                if isinstance(data, int):
                    out.append(data)
        return out

    def _rebuild_params(self, cmd: str) -> None:
        while self.params_form.rowCount() > 0:
            self.params_form.removeRow(0)
        self._param_widgets.clear()

        schema = COMMAND_SCHEMAS.get(cmd, [])
        if not schema:
            self.no_params_lbl.show()
            return
        self.no_params_lbl.hide()
        for name, typ, default in schema:
            w = self._make_widget(typ, default)
            self.params_form.addRow(name, w)
            self._param_widgets[name] = w

    def _make_widget(self, typ: str, default: Any) -> QWidget:
        if typ == "u32":
            w = QSpinBox(); w.setRange(0, 2**31 - 1); w.setValue(int(default)); return w
        if typ == "u8":
            w = QSpinBox(); w.setRange(0, 255); w.setValue(int(default)); return w
        if typ == "u8_mask":
            w = QSpinBox(); w.setRange(0, 255); w.setValue(int(default))
            w.setDisplayIntegerBase(2); w.setPrefix("0b"); return w
        if typ == "json":
            return QLineEdit(json.dumps(default))
        return QLineEdit(str(default))

    def _collect_params(self) -> Optional[dict]:
        cmd = self.cmd_combo.currentText()
        schema = COMMAND_SCHEMAS.get(cmd, [])
        params: dict[str, Any] = {}
        for name, typ, _ in schema:
            w = self._param_widgets[name]
            if typ in ("u32", "u8", "u8_mask"):
                params[name] = int(w.value())
            elif typ == "json":
                try:
                    params[name] = json.loads(w.text())
                except json.JSONDecodeError as e:
                    self._append_log(f"[Error] parameter '{name}': invalid JSON: {e}")
                    return None
            else:
                params[name] = w.text()
        return params

    def _on_preset(self, _idx: int) -> None:
        data = self.preset_combo.currentData()
        if data is None:
            return
        preset = PRESETS[data]
        if "compound" in preset:
            self._append_log(
                f"[Preset] '{preset['label']}' loaded — "
                f"on send, {len(preset['compound'])} commands will be "
                f"dispatched sequentially."
            )
            return
        cmd = preset["cmd"]
        idx_c = self.cmd_combo.findText(cmd)
        if idx_c >= 0:
            self.cmd_combo.setCurrentIndex(idx_c)
        for k, v in preset.get("params", {}).items():
            w = self._param_widgets.get(k)
            if isinstance(w, QSpinBox):
                w.setValue(int(v))
            elif isinstance(w, QLineEdit):
                w.setText(json.dumps(v) if not isinstance(v, str) else v)
        self._append_log(f"[Preset] '{preset['label']}' loaded")

    def _on_send(self) -> None:
        targets = self._selected_targets()
        if not targets:
            self._append_log("[Error] no targets selected.")
            return

        preset_idx = self.preset_combo.currentData()
        if preset_idx is not None and "compound" in PRESETS[preset_idx]:
            steps = PRESETS[preset_idx]["compound"]
        else:
            params = self._collect_params()
            if params is None:
                return
            steps = [(self.cmd_combo.currentText(), params)]

        diag = self._get_diag()
        if diag is None:
            self._append_log("[Error] diag client not connected.")
            return

        self.send_btn.setEnabled(False)
        self.send_btn.setText("Sending…")

        # CRITICAL: the worker must not touch widgets, only emit signals.
        def worker() -> None:
            try:
                for cmd, params in steps:
                    self._log_signal.emit(f"→ {cmd}  params={params}  targets={targets}")
                    if cmd == "get_status":
                        for nid in targets:
                            status = diag.get_status(nid, timeout=2.0)
                            self._log_signal.emit(
                                f"   [status node={nid}] "
                                f"{json.dumps(status) if status else 'TIMEOUT'}"
                            )
                    else:
                        results = diag.inject(targets, cmd, params,
                                              wait_ack=True, timeout=3.0)
                        for nid, resp in results.items():
                            if resp is None:
                                self._log_signal.emit(f"   [ack node={nid}] TIMEOUT")
                            else:
                                self._log_signal.emit(
                                    f"   [ack node={nid}] {json.dumps(resp.raw)}"
                                )
            except Exception as e:
                self._log_signal.emit(f"[Worker error] {type(e).__name__}: {e}")
            finally:
                self._done_signal.emit()

        threading.Thread(target=worker, name="inject-worker", daemon=True).start()

    def _on_broadcast(self) -> None:
        diag = self._get_diag()
        if diag is None:
            self._append_log("[Error] diag client not connected.")
            return
        try:
            value = json.loads(self.broadcast_input.text())
        except json.JSONDecodeError as e:
            self._append_log(f"[Error] broadcast input is not valid JSON: {e}")
            return
        diag.broadcast_input(value)
        self._append_log(f"→ broadcast_input {value}")

    @Slot(str)
    def _append_log(self, text: str) -> None:
        self.response_view.appendPlainText(text)

    @Slot()
    def _on_worker_done(self) -> None:
        self.send_btn.setEnabled(True)
        self.send_btn.setText("Send command")
