#!/usr/bin/env python3
"""
Forge - AI-assisted development environment
"""

import sys

from PySide6.QtWidgets import QApplication

from src.ui.main_window import MainWindow


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("Forge")
    app.setOrganizationName("Forge")

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
