"""
Main window: owns the core objects (DiagClient, OperationalListener,
Registry, SessionLogger) and the tabs. Handles reconnect logic when
settings change.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from PySide6.QtCore import QSettings
from PySide6.QtWidgets import QMainWindow, QStatusBar, QTabWidget

from ..core.diag_client import DiagClient, DiagTelegram
from ..core.node_registry import NodeRegistry
from ..core.operational_listener import OperationalListener
from ..core.session_logger import SessionLogger
from ..core.settings import AppSettings, load_from_qsettings, save_to_qsettings
from ..core.wire_decoder import DecodedFrame

from .config_tab import ConfigTab
from .inject_tab import InjectTab
from .logging_tab import LoggingTab
from .package_tab import PackageTab
from .settings_tab import SettingsTab
from .status_tab import StatusTab
from .test_tab import TestTab
from .timing_tab import TimingTab
from .signals import Bus


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("MooN framework diagnostic tool")
        self.resize(1200, 800)
        self.setStatusBar(QStatusBar())

        self.qsettings = QSettings("2oo3-framework", "diag-tool")
        self.settings: AppSettings = load_from_qsettings(self.qsettings)

        # Core objects (no Qt dependency)
        self.registry = NodeRegistry()
        self.session_logger = SessionLogger(Path(self.settings.session_log_dir))
        self.diag: Optional[DiagClient] = None
        self.op_listener: Optional[OperationalListener] = None

        # Bus translates thread callbacks to Qt signals
        self.bus = Bus()

        # Tabs
        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)

        self.status_tab = StatusTab(self.registry, lambda: self.diag)
        self.inject_tab = InjectTab(self.registry, lambda: self.diag)
        self.logging_tab = LoggingTab(self.session_logger)
        self.test_tab = TestTab(
            settings_provider=lambda: self.settings,
            settings_saver=self._apply_settings,
        )
        self.timing_tab = TimingTab(
            settings_provider=lambda: self.settings,
            settings_saver=self._apply_settings,
        )
        self.config_tab = ConfigTab(lambda: self.settings)
        self.package_tab = PackageTab(
            settings_provider=lambda: self.settings,
            settings_saver=self._apply_settings,
        )
        self.settings_tab = SettingsTab(lambda: self.settings)
        self.settings_tab.settings_applied.connect(self._apply_settings)

        self.tabs.addTab(self.status_tab, "Status")
        self.tabs.addTab(self.inject_tab, "Inject")
        self.tabs.addTab(self.logging_tab, "Log")
        self.tabs.addTab(self.test_tab, "Tests")
        self.tabs.addTab(self.timing_tab, "Timing")
        self.tabs.addTab(self.package_tab, "Package")
        self.tabs.addTab(self.config_tab, "Config")
        self.tabs.addTab(self.settings_tab, "Settings")

        # Wire the Bus into the tabs
        self.bus.op_frame.connect(self.logging_tab.on_op_frame)
        self.bus.diag_telegram.connect(self.logging_tab.on_diag_telegram)

        # Bring up the listeners
        self._connect_all()

    # ---- connect / disconnect --------------------------------------------

    def _connect_all(self) -> None:
        self._disconnect_all()
        s = self.settings

        try:
            self.diag = DiagClient(
                multicast_group=s.diag_group,
                port=s.diag_port,
                interface_ip=s.interface_ip,
            )
            self.diag.add_listener(self._on_diag)
            self.diag.start()
        except OSError as e:
            self.statusBar().showMessage(f"Diag client error: {e}", 5000)
            self.diag = None

        try:
            self.op_listener = OperationalListener(
                multicast_group=s.op_group,
                port=s.op_port,
                interface_ip=s.interface_ip,
            )
            self.op_listener.add_listener(self._on_op_frame)
            self.op_listener.start()
        except OSError as e:
            self.statusBar().showMessage(f"Operational listener error: {e}", 5000)
            self.op_listener = None

        self.statusBar().showMessage(
            f"Diag: {s.diag_group}:{s.diag_port}  |  "
            f"Op: {s.op_group}:{s.op_port}  |  iface: {s.interface_ip}",
            0,
        )

    def _disconnect_all(self) -> None:
        if self.diag is not None:
            self.diag.stop()
            self.diag = None
        if self.op_listener is not None:
            self.op_listener.stop()
            self.op_listener = None

    def _apply_settings(self, new_settings: AppSettings) -> None:
        self.settings = new_settings
        save_to_qsettings(self.qsettings, new_settings)
        # Reanchor the session logger (base dir may have changed)
        was_active = self.session_logger.is_active()
        if was_active:
            self.session_logger.stop()
        self.session_logger.base_dir = Path(new_settings.session_log_dir)
        if was_active:
            self.session_logger.start()
        self._connect_all()

    # ---- callbacks from core (worker threads!) ---------------------------

    def _on_op_frame(self, frame: DecodedFrame, ts_mono: float) -> None:
        # Update registry (thread-safe)
        if frame.error is None or frame.crc_ok:
            self.registry.observe_operational(
                frame.node_id, frame.node_state_wire, frame.node_state_name,
                frame.seq_num, frame.session_id,
            )
        # Session log (internally thread-safe)
        if self.session_logger.is_active():
            self.session_logger.log_operational(frame, ts_mono)
        # Emit through the Bus (queued to the UI thread)
        self.bus.op_frame.emit(frame, ts_mono)

    def _on_diag(self, tel: DiagTelegram) -> None:
        # Status responses feed the registry (even if we didn't request them
        # ourselves -- every piece of info helps the overview).
        if tel.kind == "status" and tel.source_node_id is not None:
            data = tel.raw.get("data") or {}
            self.registry.observe_diag_status(tel.source_node_id, data)
        elif tel.source_node_id is not None:
            # For staged/error we only register "this node is alive".
            v = self.registry.get(tel.source_node_id)
            if v is None:
                self.registry.observe_operational(
                    tel.source_node_id, 0, "?", 0, 0,
                )
        if self.session_logger.is_active():
            self.session_logger.log_diag(tel.raw, tel.ts_mono)
        self.bus.diag_telegram.emit(tel)

    # ---- cleanup ---------------------------------------------------------

    def closeEvent(self, event) -> None:
        self._disconnect_all()
        if self.session_logger.is_active():
            self.session_logger.stop()
        super().closeEvent(event)
