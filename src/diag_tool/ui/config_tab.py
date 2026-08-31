"""
Config tab — generate node.toml files for one or many nodes.

Layout:
  Top:    Common parameters (shared across all generated nodes:
          nominal, minimum, cycle_ms, multicast groups, ports).
  Middle: Node range (from_id .. to_id, inclusive). One TOML per id,
          with own_id filled in. Interface can be per-id or shared.
  Right:  Live preview of the currently selected node's TOML (with the
          [integrity] checksum computed). Verify button re-parses and
          re-hashes to confirm round-trip.
  Bottom: Output directory + "Write files" button.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from harness.config_gen import (
    ChecksumError,
    make_spec,
    render_toml_str,
    verify_toml_str,
)


class ConfigTab(QWidget):
    def __init__(self, settings_provider, parent=None) -> None:
        super().__init__(parent)
        self._get_settings = settings_provider
        self._build()
        # Prime the preview.
        self._on_any_change()

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 20, 20, 20); outer.setSpacing(14)

        head = QHBoxLayout()
        title = QLabel("Config generator"); title.setProperty("heading", True)
        subtitle = QLabel("Writes node_<id>.toml files with [integrity] SHA-256 checksum")
        subtitle.setProperty("muted", True)
        head.addWidget(title); head.addWidget(subtitle); head.addStretch(1)
        outer.addLayout(head)

        split = QSplitter(Qt.Horizontal)
        split.setChildrenCollapsible(False)

        # --- LEFT: parameters ---
        left = QWidget()
        ll = QVBoxLayout(left)
        ll.setContentsMargins(0, 0, 0, 0); ll.setSpacing(12)

        # Range
        range_box = QGroupBox("Node id range")
        rf = QFormLayout(range_box); rf.setContentsMargins(12, 18, 12, 12); rf.setSpacing(8)
        self.id_from = QSpinBox(); self.id_from.setRange(0, 255); self.id_from.setValue(0)
        self.id_to   = QSpinBox(); self.id_to.setRange(0, 255);   self.id_to.setValue(2)
        self.id_from.valueChanged.connect(self._refresh_node_list)
        self.id_to.valueChanged.connect(self._refresh_node_list)
        rf.addRow("from id", self.id_from)
        rf.addRow("to id (inclusive)", self.id_to)
        ll.addWidget(range_box)

        # Participants + timing
        part_box = QGroupBox("Participants && timing")
        pf = QFormLayout(part_box); pf.setContentsMargins(12, 18, 12, 12); pf.setSpacing(8)
        self.nominal = QSpinBox(); self.nominal.setRange(2, 16); self.nominal.setValue(3)
        self.minimum = QSpinBox(); self.minimum.setRange(1, 16); self.minimum.setValue(2)
        self.cycle_ms = QSpinBox(); self.cycle_ms.setRange(1, 10000); self.cycle_ms.setValue(20); self.cycle_ms.setSuffix(" ms")
        self.probation = QSpinBox(); self.probation.setRange(1, 10000); self.probation.setValue(10)
        self.init_sync_timeout = QSpinBox(); self.init_sync_timeout.setRange(1, 600_000)
        self.init_sync_timeout.setValue(2000); self.init_sync_timeout.setSuffix(" ms")
        self.init_sync_timeout.setToolTip(
            "How long a node waits at startup for its peers before giving "
            "up. Real hardware often needs this much higher than the 2000 ms "
            "default -- nodes power up at different times and boot slower "
            "than a local/simulated run."
        )
        pf.addRow("nominal", self.nominal)
        pf.addRow("minimum", self.minimum)
        pf.addRow("cycle_duration_ms", self.cycle_ms)
        pf.addRow("probation_cycles", self.probation)
        pf.addRow("init_sync_timeout_ms", self.init_sync_timeout)
        ll.addWidget(part_box)

        # Transport / diagnostic
        tp_box = QGroupBox("Transport && diagnostic")
        tf = QFormLayout(tp_box); tf.setContentsMargins(12, 18, 12, 12); tf.setSpacing(8)
        self.interface = QLineEdit("lo")
        self.fabric_group = QLineEdit("239.10.0.1")
        self.fabric_port = QSpinBox(); self.fabric_port.setRange(1, 65535); self.fabric_port.setValue(5555)
        self.diag_group = QLineEdit("239.10.0.2")
        self.diag_port = QSpinBox(); self.diag_port.setRange(1, 65535); self.diag_port.setValue(6666)
        tf.addRow("interface", self.interface)
        tf.addRow("operational multicast group", self.fabric_group)
        tf.addRow("operational port", self.fabric_port)
        tf.addRow("diagnostic multicast group", self.diag_group)
        tf.addRow("diagnostic port", self.diag_port)
        ll.addWidget(tp_box)

        # Prefill from GUI settings
        s = self._get_settings()
        self.diag_group.setText(s.diag_group); self.diag_port.setValue(s.diag_port)
        self.fabric_group.setText(s.op_group); self.fabric_port.setValue(s.op_port)

        # Any field change refreshes preview.
        for w in (self.nominal, self.minimum, self.cycle_ms, self.probation,
                  self.init_sync_timeout, self.fabric_port, self.diag_port,
                  self.id_from, self.id_to):
            w.valueChanged.connect(self._on_any_change)
        for w in (self.interface, self.fabric_group, self.diag_group):
            w.textChanged.connect(self._on_any_change)

        ll.addStretch(1)
        split.addWidget(left)

        # --- MIDDLE: node picker ---
        mid = QWidget()
        ml = QVBoxLayout(mid); ml.setContentsMargins(0, 0, 0, 0); ml.setSpacing(6)
        h = QLabel("Preview"); h.setProperty("heading", True)
        ml.addWidget(h)
        self.node_list = QListWidget()
        self.node_list.currentRowChanged.connect(self._on_any_change)
        ml.addWidget(self.node_list, 1)
        split.addWidget(mid)

        # --- RIGHT: preview + verify + write ---
        right = QWidget()
        rl = QVBoxLayout(right); rl.setContentsMargins(0, 0, 0, 0); rl.setSpacing(8)

        self.preview = QTextEdit()
        self.preview.setReadOnly(True)
        mono = QFont("JetBrains Mono, Menlo, Consolas, monospace", 9)
        mono.setStyleHint(QFont.Monospace)
        self.preview.setFont(mono)
        self.preview.setLineWrapMode(QTextEdit.NoWrap)
        rl.addWidget(self.preview, 1)

        actions = QHBoxLayout()
        self.status_lbl = QLabel("—"); self.status_lbl.setProperty("muted", True)
        actions.addWidget(self.status_lbl); actions.addStretch(1)
        self.verify_btn = QPushButton("Verify checksum")
        self.verify_btn.clicked.connect(self._on_verify)
        self.write_btn = QPushButton("Write files…")
        self.write_btn.setProperty("accent", True)
        self.write_btn.clicked.connect(self._on_write)
        actions.addWidget(self.verify_btn); actions.addWidget(self.write_btn)
        rl.addLayout(actions)

        split.addWidget(right)
        split.setStretchFactor(0, 2)
        split.setStretchFactor(1, 1)
        split.setStretchFactor(2, 3)
        outer.addWidget(split, 1)

        self._refresh_node_list()

    # ---- range list ------------------------------------------------------

    def _refresh_node_list(self) -> None:
        current = self.node_list.currentRow()
        self.node_list.blockSignals(True)
        self.node_list.clear()
        lo, hi = self.id_from.value(), self.id_to.value()
        if lo > hi:
            lo, hi = hi, lo
        for nid in range(lo, hi + 1):
            self.node_list.addItem(QListWidgetItem(f"node_{nid}.toml"))
        self.node_list.setCurrentRow(min(max(current, 0), self.node_list.count() - 1))
        self.node_list.blockSignals(False)
        self._on_any_change()

    def _selected_node_id(self) -> int:
        row = self.node_list.currentRow()
        lo, hi = self.id_from.value(), self.id_to.value()
        if lo > hi:
            lo, hi = hi, lo
        row = max(0, row)
        return lo + row

    # ---- preview + verify ------------------------------------------------

    def _build_spec(self, own_id: int):
        return make_spec(
            own_id=own_id,
            nominal=self.nominal.value(),
            minimum=self.minimum.value(),
            cycle_ms=self.cycle_ms.value(),
            fabric_group=self.fabric_group.text().strip() or "239.10.0.1",
            fabric_port=self.fabric_port.value(),
            diag_group=self.diag_group.text().strip() or "239.10.0.2",
            diag_port=self.diag_port.value(),
            interface=self.interface.text().strip() or "lo",
            init_sync_timeout_ms=self.init_sync_timeout.value(),
        )

    def _on_any_change(self) -> None:
        if self.node_list.count() == 0:
            self.preview.clear()
            return
        try:
            spec = self._build_spec(self._selected_node_id())
            # spec has probation baked into the default, override to whatever
            # is in the field.
            from dataclasses import replace as _replace
            spec = _replace(spec, probation_cycles=self.probation.value())
            text = render_toml_str(spec)
        except Exception as e:  # noqa: BLE001
            self.preview.setPlainText(f"[render error] {type(e).__name__}: {e}")
            self._set_status(f"render failed: {e}", "danger")
            return
        self.preview.setPlainText(text)
        # auto-verify to catch mistakes in render vs. checksum
        try:
            verify_toml_str(text)
            self._set_status("checksum ✓", "success")
        except ChecksumError as e:
            self._set_status(f"checksum invalid: {e}", "danger")

    def _on_verify(self) -> None:
        text = self.preview.toPlainText()
        try:
            cfg = verify_toml_str(text)
            self._set_status(
                f"OK · algo={cfg['integrity']['algo']} "
                f"checksum={cfg['integrity']['checksum'][:12]}…",
                "success",
            )
        except ChecksumError as e:
            self._set_status(f"invalid: {e}", "danger")
            QMessageBox.warning(self, "Verify failed", str(e))

    # ---- write files -----------------------------------------------------

    def _on_write(self) -> None:
        s = self._get_settings()
        default_dir = str(Path(s.session_log_dir).parent / "configs")
        chosen = QFileDialog.getExistingDirectory(
            self, "Choose output directory", default_dir,
        )
        if not chosen:
            return
        out_dir = Path(chosen)
        lo, hi = self.id_from.value(), self.id_to.value()
        if lo > hi:
            lo, hi = hi, lo

        written: list[Path] = []
        failed: list[tuple[int, str]] = []
        for nid in range(lo, hi + 1):
            try:
                from dataclasses import replace as _replace
                spec = _replace(self._build_spec(nid),
                                probation_cycles=self.probation.value())
                text = render_toml_str(spec)
                # round-trip self-check
                verify_toml_str(text)
                p = out_dir / f"node_{nid}.toml"
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(text)
                written.append(p)
            except Exception as e:  # noqa: BLE001
                failed.append((nid, str(e)))

        if failed:
            msg = "\n".join(f"node {n}: {e}" for n, e in failed)
            QMessageBox.critical(self, "Some files failed",
                                 f"{len(written)} written, {len(failed)} failed:\n\n{msg}")
        else:
            QMessageBox.information(
                self, "Done",
                f"Wrote {len(written)} config(s) to {out_dir}",
            )
        self._set_status(f"wrote {len(written)} file(s) to {out_dir}", "success")

    # ---- helpers ---------------------------------------------------------

    def _set_status(self, text: str, kind: str) -> None:
        self.status_lbl.setText(text)
        colors = {
            "success": QColor("#23A55A"),
            "danger":  QColor("#DA373C"),
            "muted":   QColor("#949BA4"),
        }
        self.status_lbl.setStyleSheet(f"color: {colors.get(kind, QColor('#DBDEE1')).name()};")
