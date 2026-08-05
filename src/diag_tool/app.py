"""QApplication-Bootstrap."""

from __future__ import annotations

import os
import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication

from diag_tool.config import AppConfig
from diag_tool.ui.main_window import MainWindow


def run(argv: list[str]) -> int:
    # Konsistentes HiDPI-Rundungsverhalten über alle Screens
    QGuiApplication.setHighDpiScaleFactorRoundingPolicy(
        Qt.HighDpiScaleFactorRoundingPolicy.PassThrough
    )
    # X11: Wayland/Xwayland-Skalierung nicht doppelt anwenden
    os.environ.setdefault("QT_ENABLE_HIGHDPI_SCALING", "1")

    app = QApplication(argv)
    app.setApplicationName("diag-tool")
    app.setOrganizationName("diag-tool")

    config = AppConfig.load()

    window = MainWindow(config)
    window.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(run(sys.argv))
