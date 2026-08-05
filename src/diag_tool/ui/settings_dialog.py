"""SettingsDialog — Multicast-Endpunkte und Logging-Verzeichnis anzeigen/anpassen.

Persistiert Änderungen als TOML in ``$XDG_CONFIG_HOME/diag-tool/config.toml``.
"""

from __future__ import annotations

from pathlib import Path

import tomli_w
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from diag_tool.config import CONFIG_DIR, AppConfig


class SettingsDialog(QDialog):
    def __init__(self, config: AppConfig, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._config = config
        self.setWindowTitle("Einstellungen")
        self.setMinimumWidth(480)

        layout = QVBoxLayout(self)

        # ------------------------------------------------------ System-Multicast
        sys_box = QGroupBox("Betriebs-Multicast (passiv)")
        sys_form = QFormLayout(sys_box)
        self._sys_group = QLineEdit(str(config.network.system.group))
        self._sys_port = QSpinBox()
        self._sys_port.setRange(1, 65535)
        self._sys_port.setValue(config.network.system.port)
        self._sys_iface = QLineEdit(config.network.system.interface)
        sys_form.addRow("Gruppe:", self._sys_group)
        sys_form.addRow("Port:", self._sys_port)
        sys_form.addRow("Interface:", self._sys_iface)
        layout.addWidget(sys_box)

        # ------------------------------------------------------ Diagnose-Multicast
        diag_box = QGroupBox("Diagnose-Multicast (bidirektional)")
        diag_form = QFormLayout(diag_box)
        self._diag_group = QLineEdit(str(config.network.diagnose.group))
        self._diag_port = QSpinBox()
        self._diag_port.setRange(1, 65535)
        self._diag_port.setValue(config.network.diagnose.port)
        self._diag_iface = QLineEdit(config.network.diagnose.interface)
        self._diag_ttl = QSpinBox()
        self._diag_ttl.setRange(1, 255)
        self._diag_ttl.setValue(config.network.diagnose.ttl)
        diag_form.addRow("Gruppe:", self._diag_group)
        diag_form.addRow("Port:", self._diag_port)
        diag_form.addRow("Interface:", self._diag_iface)
        diag_form.addRow("TTL:", self._diag_ttl)
        layout.addWidget(diag_box)

        # ------------------------------------------------------ Logging
        log_box = QGroupBox("Session-Log")
        log_form = QFormLayout(log_box)
        self._log_dir = QLineEdit(str(config.logging.session_dir))
        browse = QPushButton("Wählen…")
        browse.clicked.connect(self._pick_dir)
        row = QHBoxLayout()
        row.addWidget(self._log_dir, 1)
        row.addWidget(browse)
        log_form.addRow("Verzeichnis:", row)
        layout.addWidget(log_box)

        # ------------------------------------------------------ Buttons
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save
            | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._on_save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _pick_dir(self) -> None:
        path = QFileDialog.getExistingDirectory(
            self, "Session-Log-Verzeichnis", self._log_dir.text()
        )
        if path:
            self._log_dir.setText(path)

    def _on_save(self) -> None:
        data = {
            "network": {
                "system": {
                    "group": self._sys_group.text(),
                    "port": self._sys_port.value(),
                    "interface": self._sys_iface.text(),
                },
                "diagnose": {
                    "group": self._diag_group.text(),
                    "port": self._diag_port.value(),
                    "interface": self._diag_iface.text(),
                    "ttl": self._diag_ttl.value(),
                },
            },
            "logging": {
                "session_dir": self._log_dir.text(),
            },
        }
        target_dir = CONFIG_DIR()
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / "config.toml"
        with target.open("wb") as f:
            tomli_w.dump(data, f)
        # Live-Update der aktuellen Config (Endpunkte greifen erst mit neuer Session)
        _ = Path
        self.accept()
