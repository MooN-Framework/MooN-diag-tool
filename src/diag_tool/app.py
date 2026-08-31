"""Entry point: launches QApplication + MainWindow."""
from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from .ui.main_window import MainWindow
from .ui import style


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("MooN framework diagnostic tool")
    app.setOrganizationName("MooN-framework")
    style.apply(app)
    w = MainWindow()
    w.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
