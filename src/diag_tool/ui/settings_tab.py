"""
Settings tab: edits AppSettings. Changes take effect after "Apply" --
this triggers a listener reconnect.
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from ..core.os_open import OpenError, open_path
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
        self.iface.setToolTip(
            "This GUI machine's own bind address for the multicast "
            "groups below (watching a hardware sweep, Status/Inject/Log)."
        )
        self.node_iface = QLineEdit(s.node_network_interface)
        self.node_iface.setToolTip(
            "Network interface NAME (not IP), e.g. eth0 -- baked into a "
            "node's own config.toml on hardware deploy. This is what the "
            "Rust binary on the Pi itself binds to, not this machine. "
            "Simulated mode always uses 'lo' regardless of this."
        )
        self.diag_group = QLineEdit(s.diag_group)
        self.diag_port = QSpinBox(); self.diag_port.setRange(1, 65535); self.diag_port.setValue(s.diag_port)
        self.op_group = QLineEdit(s.op_group)
        self.op_port = QSpinBox(); self.op_port.setRange(1, 65535); self.op_port.setValue(s.op_port)
        nf.addRow("Interface IP", self.iface)
        nf.addRow("Node network interface", self.node_iface)
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
        self.pi_gen_repo = QLineEdit(s.moon_pi_gen_repo_path)
        self.pi_gen_repo.setToolTip(
            "Checkout of the pi-gen/moon-pi-images repo -- needed to locate "
            "tools/build-moon-package.sh for the Package tab."
        )
        self.signing_key = QLineEdit(s.moon_signing_key_path)
        self.signing_key.setToolTip(
            "Path to the ed25519 private signing key .pem used by the "
            "Package tab to sign .moonpkg packages (openssl pkeyutl -sign). "
            "Only prod (signed) packages are supported -- keep this key off "
            "the nodes themselves, it's not something they need."
        )
        pf.addRow("Session logs", self._with_browse(self.session_dir, openable=True))
        pf.addRow("Rust repository", self._with_browse(self.rust_repo))
        pf.addRow("pi-gen repository", self._with_browse(self.pi_gen_repo))
        pf.addRow("Package signing key", self._with_browse(self.signing_key, is_file=True))
        outer.addWidget(paths)

        # Test mode -- by default still derived automatically (see
        # Test/Timing tab) from whether any hardware node is
        # configured+enabled. This dropdown overrides that when you
        # want to run a quick simulated check without disabling all
        # your hardware nodes first, or vice versa.
        tm = QGroupBox("Test mode")
        tf = QFormLayout(tm); tf.setContentsMargins(12, 20, 12, 12); tf.setSpacing(10)
        self.test_mode = QComboBox()
        self.test_mode.addItem("Auto (derive from configured hardware nodes)", "auto")
        self.test_mode.addItem("Force simulated", "simulated")
        self.test_mode.addItem("Force hardware", "hardware")
        idx = self.test_mode.findData(s.test_mode)
        self.test_mode.setCurrentIndex(idx if idx >= 0 else 0)
        hint = QLabel(
            "<span style='color:#949BA4'>"
            "<b>Auto</b>: simulated with 0 configured+enabled hardware nodes, "
            "hardware with &ge;1 ('Configure hardware nodes…' in the Test/"
            "Timing tab).<br>"
            "<b>Force simulated</b>: pytest/sweep always spawns nodes locally "
            "via cargo, even if hardware nodes are configured.<br>"
            "<b>Force hardware</b>: always uses the configured nodes over the "
            "multicast groups + SSH deploy -- fails clearly if none are "
            "configured+enabled."
            "</span>"
        )
        hint.setTextFormat(Qt.RichText)
        hint.setWordWrap(True)
        tf.addRow("Mode", self.test_mode)
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

    def _with_browse(self, line: QLineEdit, openable: bool = False, is_file: bool = False) -> QWidget:
        wrap = QWidget()
        h = QHBoxLayout(wrap); h.setContentsMargins(0, 0, 0, 0); h.setSpacing(6)
        h.addWidget(line)
        btn = QPushButton("…"); btn.setFixedWidth(36)
        def pick() -> None:
            if is_file:
                p, _ = QFileDialog.getOpenFileName(
                    self, "Choose file", line.text() or str(Path.home())
                )
            else:
                p = QFileDialog.getExistingDirectory(
                    self, "Choose directory", line.text() or str(Path.home())
                )
            if p:
                line.setText(p)
        btn.clicked.connect(pick)
        h.addWidget(btn)
        if openable:
            open_btn = QPushButton("Open")
            def do_open() -> None:
                try:
                    p = Path(line.text() or ".")
                    p.mkdir(parents=True, exist_ok=True)
                    open_path(p)
                except OpenError as e:
                    QMessageBox.warning(self, "Could not open folder", str(e))
            open_btn.clicked.connect(do_open)
            h.addWidget(open_btn)
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
            node_network_interface=self.node_iface.text().strip() or "eth0",
            session_log_dir=self.session_dir.text().strip(),
            rust_repo_path=self.rust_repo.text().strip(),
            moon_pi_gen_repo_path=self.pi_gen_repo.text().strip(),
            moon_signing_key_path=self.signing_key.text().strip(),
            scenarios_path="",  # obsolete: tests live in this repo now
            test_mode=self.test_mode.currentData(),
        )
        self.settings_applied.emit(s)
