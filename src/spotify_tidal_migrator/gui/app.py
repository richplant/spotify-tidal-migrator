from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication

from .. import paths
from .main_window import MainWindow


def main() -> None:
    paths.ensure_dirs()
    app = QApplication(sys.argv)
    app.setStyleSheet("QPushButton { padding: 6px 16px; min-height: 26px; }")
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
