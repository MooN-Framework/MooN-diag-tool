"""
Settings tab: edits AppSettings. Changes take effect after "Apply" --
this triggers a listener reconnect, and (via Bus.settings_changed in
main_window) every other tab reactively re-syncs its mode label,
target-node dropdown, etc. -- no app restart needed for a
simulated/hardware mode switch, whether it's triggered by Apply here
or by enabling/disabling nodes in the "Configure hardware nodes…"
dialog (Test/Timing/Package tab).
"""
from __future__ import annotations

from dataclasses import replace
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
    QScrollArea,
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

        Bug fix (kept from before): _on_apply() used to build a
        brand-new AppSettings from only the fields shown in this tab,
        so every "Apply && reconnect" silently reset everything else
        (hardware_nodes_json, ...) back to its default -- that's why
        the hardware node list kept disappearing. Fetching the live
        settings at apply-time (rather than the `initial` snapshot
        from construction, which could already be stale by then) and
        dataclasses.replace()-ing only the fields this tab actually
        edits fixes that for good.
        """
        super().__init__(parent)
        self._get_settings = settings_provider
        self._loading = False
        # Snapshot of "what's actually applied, as far as this tab is
        # concerned" -- NOT re-read from self._get_settings() on every
        # dirty-check. Kept in sync via on_settings_changed instead, so
        # that comparing widget values against a moving live target
        # can't produce a false "dirty" the moment some OTHER field
        # (e.g. hardware_nodes_json, changed from another tab's dialog)
        # changes underneath this tab -- see on_settings_changed.
        self._baseline = settings_provider()
        self._build(self._baseline)
        self._refresh_dirty_state()

    def _build(self, s: AppSettings) -> None:
        # The unreadable Network inputs were a stylesheet problem, fixed
        # by the input min-height in style.py, not by this scroll area.
        # The scroll area is here for a separate reason: this page
        # stacks four group boxes and is the tallest in the app, and now
        # that every input is 32px instead of 21px it is taller still,
        # so on a small window the bottom rows and the Apply button
        # would otherwise be unreachable.
        page_layout = QVBoxLayout(self)
        page_layout.setContentsMargins(0, 0, 0, 0)
        page_layout.setSpacing(0)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QScrollArea.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        page_layout.addWidget(scroll)

        page = QWidget()
        scroll.setWidget(page)

        outer = QVBoxLayout(page)
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

        # Build (hardware mode) -- moved here from the Timing tab, which
        # used to have its own editable copy of these three fields. That
        # meant target_triple/binary_name/build_features could be typed
        # into two different places with no indication which one "won"
        # on the next build; there is now exactly one place to set them.
        # Only relevant once hardware mode is possible -- a purely
        # simulated setup builds via bare `cargo`, no target/cross
        # needed -- so this box shows/hides live as the Test mode
        # dropdown above changes (visible for "Auto" and "Force
        # hardware", hidden for "Force simulated") or as hardware
        # nodes get enabled/disabled elsewhere -- see _refresh_build_box
        # and on_settings_changed.
        self.build_box = QGroupBox("Build (hardware mode)")
        bbf = QFormLayout(self.build_box); bbf.setContentsMargins(12, 20, 12, 12); bbf.setSpacing(10)
        self.target_triple = QLineEdit(s.target_triple)
        self.target_triple.setPlaceholderText(
            "e.g. aarch64-unknown-linux-gnu (empty = host build via cargo, "
            "set = cross-compile via `cross`)"
        )
        self.binary_name = QLineEdit(s.binary_name)
        self.build_features = QLineEdit(s.build_features)
        self.build_features.setPlaceholderText("comma-separated, e.g. diagnostic")
        bbf.addRow("Target triple", self.target_triple)
        bbf.addRow("Binary name", self.binary_name)
        bbf.addRow("Features", self.build_features)
        build_hint = QLabel(
            "<span style='color:#949BA4'>Used by the \"cargo build --release\" "
            "and \"Deploy to all hardware nodes\" actions in the Timing tab, "
            "and by the Test tab's \"Deploy binary + config before run\".</span>"
        )
        build_hint.setTextFormat(Qt.RichText)
        build_hint.setWordWrap(True)
        bbf.addRow(build_hint)
        outer.addWidget(self.build_box)

        # Apply
        btns = QHBoxLayout()
        self.dirty_lbl = QLabel("")
        self.dirty_lbl.setProperty("muted", True)
        btns.addWidget(self.dirty_lbl)
        btns.addStretch(1)
        self.apply_btn = QPushButton("Apply && reconnect")
        self.apply_btn.setProperty("accent", True)
        self.apply_btn.clicked.connect(self._on_apply)
        btns.addWidget(self.apply_btn)
        outer.addLayout(btns)
        outer.addStretch(1)

        # Any field change re-evaluates the dirty/unsaved-changes state
        # (which highlights Apply -- see _refresh_dirty_state) and, for
        # the mode dropdown specifically, also the Build box's
        # visibility -- both live, before Apply is even clicked.
        for w in (self.iface, self.node_iface, self.diag_group, self.op_group,
                  self.session_dir, self.rust_repo, self.pi_gen_repo,
                  self.signing_key, self.target_triple, self.binary_name,
                  self.build_features):
            w.textChanged.connect(self._on_field_changed)
        for w in (self.diag_port, self.op_port):
            w.valueChanged.connect(self._on_field_changed)
        self.test_mode.currentIndexChanged.connect(self._on_field_changed)

        self._refresh_build_box()

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

    # ---- pending settings / dirty tracking --------------------------------

    def _pending_settings(self, current: AppSettings) -> AppSettings:
        """Build the AppSettings this tab's widgets currently describe,
        layered on top of `current` (so fields this tab doesn't show --
        e.g. hardware_nodes_json -- survive untouched). Shared by
        _on_apply() (the settings actually emitted) and
        _is_dirty()/_refresh_dirty_state() (what "unsaved changes" is
        measured against), so both always agree on what "pending"
        means."""
        return replace(
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
            target_triple=self.target_triple.text().strip(),
            binary_name=self.binary_name.text().strip() or "node",
            build_features=self.build_features.text().strip(),
        )

    def _on_field_changed(self, *_args) -> None:
        if self._loading:
            return
        self._refresh_build_box()
        self._refresh_dirty_state()

    def _is_dirty(self) -> bool:
        return self._pending_settings(self._baseline) != self._baseline

    def _refresh_dirty_state(self) -> None:
        dirty = self._is_dirty()
        # QSS re-evaluates a dynamic property only after a style
        # unpolish/polish cycle -- just calling setProperty() leaves
        # the old rendered state on screen until something else (a
        # hover, a resize) forces Qt to repaint it.
        self.apply_btn.setProperty("dirty", dirty)
        self.apply_btn.style().unpolish(self.apply_btn)
        self.apply_btn.style().polish(self.apply_btn)
        self.apply_btn.setText("Apply && reconnect ●" if dirty else "Apply && reconnect")
        self.dirty_lbl.setText("unsaved changes" if dirty else "up to date")

    def _refresh_build_box(self) -> None:
        """Show/enable the hardware build fields whenever hardware mode
        is possible right now -- not just once it's actually active.
        "Auto" can flip to hardware the moment a node gets enabled
        elsewhere, so the fields stay available under "Auto" too
        (pre-fill the target triple before you've configured any
        hardware node at all); only an explicit "Force simulated"
        hides them, since that's a deliberate statement that hardware
        building doesn't apply right now."""
        mode_choice = self.test_mode.currentData()
        self.build_box.setVisible(mode_choice != "simulated")

    def _load_fields(self, s: AppSettings) -> None:
        """(Re)populate every widget from `s`. Used both at construction
        and from on_settings_changed when this tab has no unsaved edits
        of its own -- see there for why that condition matters."""
        self._loading = True
        try:
            self.iface.setText(s.interface_ip)
            self.node_iface.setText(s.node_network_interface)
            self.diag_group.setText(s.diag_group)
            self.diag_port.setValue(s.diag_port)
            self.op_group.setText(s.op_group)
            self.op_port.setValue(s.op_port)
            self.session_dir.setText(s.session_log_dir)
            self.rust_repo.setText(s.rust_repo_path)
            self.pi_gen_repo.setText(s.moon_pi_gen_repo_path)
            self.signing_key.setText(s.moon_signing_key_path)
            idx = self.test_mode.findData(s.test_mode)
            self.test_mode.setCurrentIndex(idx if idx >= 0 else 0)
            self.target_triple.setText(s.target_triple)
            self.binary_name.setText(s.binary_name)
            self.build_features.setText(s.build_features)
        finally:
            self._loading = False

    # ---- reactive updates from elsewhere -----------------------------------

    def on_settings_changed(self, s: AppSettings) -> None:
        """Connected to Bus.settings_changed in main_window -- fires on
        every settings apply, including this tab's own (harmless
        no-op re-sync) and, more importantly, a hardware-nodes dialog
        save from the Test/Timing/Package tab: with "Auto" mode
        selected, enabling/disabling hardware nodes there changes
        whether this tab's Build box should be showing, and this is
        what keeps that in sync without needing to leave and re-enter
        the Settings tab.

        `self._baseline` always adopts `s` -- it needs to, so a field
        this tab doesn't show (hardware_nodes_json) that changed
        elsewhere is correctly reflected in the next dirty-check.
        Only the WIDGETS get reloaded from `s`, and only if this tab
        has no unsaved edits of its own: otherwise an external apply
        would silently discard whatever the user was mid-typing here,
        and (before this baseline split existed) comparing stale
        widget values against a freshly-changed live settings object
        could flag "dirty" for a field the user never touched at all.
        """
        was_dirty = self._is_dirty()
        self._baseline = s
        if not was_dirty:
            self._load_fields(s)
        self._refresh_build_box()
        self._refresh_dirty_state()

    # ---- apply --------------------------------------------------------------

    def _on_apply(self) -> None:
        s = self._pending_settings(self._baseline)
        self._baseline = s
        self.settings_applied.emit(s)
        # main_window._apply_settings() -> Bus.settings_changed will
        # also call on_settings_changed(s) above (a no-op here since
        # self._baseline already equals s); refresh eagerly too so the
        # button updates even if that broadcast were ever skipped.
        self._refresh_dirty_state()
