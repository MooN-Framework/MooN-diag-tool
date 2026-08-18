"""
Settings tab: edits AppSettings. Changes take effect after "Apply" --
this triggers a listener reconnect.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..core.settings import AppSettings


class SettingsTab(QWidget):
    settings_applied = Signal(object)

    def __init__(self, settings_provider, parent=None) -> None:
        """
        settings_provider() -> current AppSettings.

        Bug fix: _on_apply() used to build a brand-new AppSettings from
        only the fields shown in this tab, so every "Apply && reconnect"
        silently reset everything else (hardware_nodes_json,
        target_triple, binary_name, build_features, ...) back to its
        default -- that's why the hardware node list kept disappearing.
        Fetching the live settings at apply-time (rather than the
        `initial` snapshot from construction, which could already be
        stale by then) and dataclasses.replace()-ing only the fields
        this tab actually edits fixes that for good.
        """
        super().__init__(parent)
        self._get_settings = settings_provider
        self._build(settings_provider())

    def _build(self, s: AppSettings) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 20, 20, 20)
        outer.setSpacing(14)

        title = QLabel("Settings")
        title.setProperty("heading", True)
        outer.addWidget(title)

        # Network
        net = QGroupBox("Network")
        nf = QFormLayout(net); nf.setContentsMargins(12, 20, 12, 12); nf.setSpacing(10)
        self.iface = QLineEdit(s.interface_ip)
        self.diag_group = QLineEdit(s.diag_group)
        self.diag_port = QSpinBox(); self.diag_port.setRange(1, 65535); self.diag_port.setValue(s.diag_port)
        self.op_group = QLineEdit(s.op_group)
        self.op_port = QSpinBox(); self.op_port.setRange(1, 65535); self.op_port.setValue(s.op_port)
        nf.addRow("Interface IP", self.iface)
        nf.addRow("Diag multicast group", self.diag_group)
        nf.addRow("Diag port", self.diag_port)
        nf.addRow("Operational multicast group", self.op_group)
        nf.addRow("Operational port", self.op_port)
        outer.addWidget(net)

        # Paths
        paths = QGroupBox("Paths")
        pf = QFormLayout(paths); pf.setContentsMargins(12, 20, 12, 12); pf.setSpacing(10)
        self.session_dir = QLineEdit(s.session_log_dir)
        self.rust_repo = QLineEdit(s.rust_repo_path)
        pf.addRow("Session logs", self._with_browse(self.session_dir))
        pf.addRow("Rust repository", self._with_browse(self.rust_repo))
        outer.addWidget(paths)

        # Test mode -- no longer a manual choice, see Test/Timing tab:
        # mode is derived from whether any hardware node is configured.
        # Kept here as an info panel so it's not a mystery where the
        # switch went.
        tm = QGroupBox("Test mode")
        tf = QFormLayout(tm); tf.setContentsMargins(12, 20, 12, 12); tf.setSpacing(10)
        hint = QLabel(
            "<span style='color:#949BA4'>"
            "Mode is no longer chosen here -- it's derived automatically "
            "in the Test/Timing tab from whether any hardware node is "
            "configured ('Configure hardware nodes…'):<br>"
            "<b>simulated</b> (0 nodes): pytest spawns nodes locally via cargo.<br>"
            "<b>hardware</b> (&ge;1 node): uses the configured nodes over "
            "the multicast groups + SSH deploy."
            "</span>"
        )
        hint.setTextFormat(Qt.RichText)
        hint.setWordWrap(True)
        tf.addRow(hint)
        outer.addWidget(tm)

        # Apply
        btns = QHBoxLayout()
        btns.addStretch(1)
        self.apply_btn = QPushButton("Apply && reconnect")
        self.apply_btn.setProperty("accent", True)
        self.apply_btn.clicked.connect(self._on_apply)
        btns.addWidget(self.apply_btn)
        outer.addLayout(btns)
        outer.addStretch(1)

    def _with_browse(self, line: QLineEdit) -> QWidget:
        wrap = QWidget()
        h = QHBoxLayout(wrap); h.setContentsMargins(0, 0, 0, 0); h.setSpacing(6)
        h.addWidget(line)
        btn = QPushButton("…"); btn.setFixedWidth(36)
        def pick() -> None:
            p = QFileDialog.getExistingDirectory(
                self, "Choose directory", line.text() or str(Path.home())
            )
            if p:
                line.setText(p)
        btn.clicked.connect(pick)
        h.addWidget(btn)
        return wrap

    def _on_apply(self) -> None:
        from dataclasses import replace
        current = self._get_settings()
        s = replace(
            current,
            diag_group=self.diag_group.text().strip(),
            diag_port=self.diag_port.value(),
            op_group=self.op_group.text().strip(),
            op_port=self.op_port.value(),
            interface_ip=self.iface.text().strip(),
            session_log_dir=self.session_dir.text().strip(),
            rust_repo_path=self.rust_repo.text().strip(),
            scenarios_path="",  # obsolete: tests live in this repo now
        )
        self.settings_applied.emit(s)
