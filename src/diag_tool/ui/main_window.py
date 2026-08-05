"""MainWindow — Tabs für Monitor, Injection, Log, Einstellungen."""

from __future__ import annotations

from PySide6.QtCore import QSettings, Qt, Slot
from PySide6.QtGui import QAction, QKeySequence
from PySide6.QtWidgets import (
    QLabel,
    QMainWindow,
    QMessageBox,
    QStatusBar,
    QTabWidget,
)

from diag_tool.config import AppConfig
from diag_tool.core.session import Session
from diag_tool.ui.inject_view import InjectView
from diag_tool.ui.log_view import LogView
from diag_tool.ui.monitor_view import MonitorView
from diag_tool.ui.settings_dialog import SettingsDialog


class MainWindow(QMainWindow):
    def __init__(self, config: AppConfig) -> None:
        super().__init__()
        self._config = config
        self._session = Session(config)

        self.setWindowTitle("2oo3 Diagnose-Tool")

        self._tabs = QTabWidget()
        self._tabs.setDocumentMode(True)

        self._monitor = MonitorView()
        self._inject = InjectView()
        self._log = LogView()

        self._tabs.addTab(self._monitor, "Monitor")
        self._tabs.addTab(self._inject, "Fault Injection")
        self._tabs.addTab(self._log, "Session-Log")

        self.setCentralWidget(self._tabs)

        # Statusbar
        self._status_label = QLabel("Bereit")
        self._log_path_label = QLabel("")
        status: QStatusBar = self.statusBar()
        status.addWidget(self._status_label, 1)
        status.addPermanentWidget(self._log_path_label)

        self._build_menu()
        self._wire_session()
        self._restore_geometry()

    # ------------------------------------------------------------------ Menü
    def _build_menu(self) -> None:
        file_menu = self.menuBar().addMenu("&Datei")

        act_start = QAction("Session &starten", self)
        act_start.setShortcut(QKeySequence("Ctrl+R"))
        act_start.triggered.connect(self._start_session)
        file_menu.addAction(act_start)

        act_stop = QAction("Session &beenden", self)
        act_stop.setShortcut(QKeySequence("Ctrl+Shift+R"))
        act_stop.triggered.connect(self._stop_session)
        file_menu.addAction(act_stop)

        file_menu.addSeparator()

        act_settings = QAction("&Einstellungen…", self)
        act_settings.setShortcut(QKeySequence.StandardKey.Preferences)
        act_settings.triggered.connect(self._open_settings)
        file_menu.addAction(act_settings)

        file_menu.addSeparator()

        act_quit = QAction("&Beenden", self)
        act_quit.setShortcut(QKeySequence.StandardKey.Quit)
        act_quit.triggered.connect(self.close)
        file_menu.addAction(act_quit)

    # ------------------------------------------------------------------ Session
    def _wire_session(self) -> None:
        self._session.system_frame.connect(self._monitor.on_system_frame)
        self._session.diagnose_frame.connect(self._log.append_rx_frame)
        self._session.error.connect(self._on_error)
        self._session.log_path_changed.connect(self._on_log_path)
        self._inject.send_requested.connect(self._session.send_diagnose_frame)

    @Slot()
    def _start_session(self) -> None:
        self._session.start()
        self._status_label.setText("Session läuft")

    @Slot()
    def _stop_session(self) -> None:
        self._session.stop()
        self._status_label.setText("Session beendet")

    @Slot(str)
    def _on_error(self, msg: str) -> None:
        self._status_label.setText(f"Fehler: {msg}")

    @Slot(str)
    def _on_log_path(self, path: str) -> None:
        self._log_path_label.setText(f"Log: {path}")

    # ------------------------------------------------------------------ Settings
    @Slot()
    def _open_settings(self) -> None:
        dlg = SettingsDialog(self._config, self)
        if dlg.exec() == SettingsDialog.DialogCode.Accepted:
            QMessageBox.information(
                self,
                "Einstellungen",
                "Änderungen greifen nach Neustart der Session.",
            )

    # ------------------------------------------------------------------ Geometry
    def _restore_geometry(self) -> None:
        s = QSettings("diag-tool", "diag-tool")
        geom = s.value("mainwindow/geometry")
        if geom is not None:
            self.restoreGeometry(geom)  # type: ignore[arg-type]
        else:
            # Default: 80% des verfügbaren Screens
            screen = self.screen()
            if screen is not None:
                avail = screen.availableGeometry()
                self.resize(int(avail.width() * 0.8), int(avail.height() * 0.8))
                self.move(
                    avail.x() + (avail.width() - self.width()) // 2,
                    avail.y() + (avail.height() - self.height()) // 2,
                )

    def closeEvent(self, event) -> None:  # type: ignore[override]  # noqa: N802
        s = QSettings("diag-tool", "diag-tool")
        s.setValue("mainwindow/geometry", self.saveGeometry())
        self._session.stop()
        super().closeEvent(event)
        # Qt.WA_DeleteOnClose nicht nötig — MainWindow lebt bis QApp.exec zurückkehrt
        _ = Qt.WA_DeleteOnClose  # silence unused import if refactored
