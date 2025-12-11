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

    try:
        window = MainWindow()
        window.show()
        sys.exit(app.exec())
    except ValueError as e:
        # Not in a git repository
        from PySide6.QtWidgets import QMessageBox
        msg = QMessageBox()
        msg.setIcon(QMessageBox.Icon.Critical)
        msg.setWindowTitle("Git Repository Required")
        msg.setText("Forge requires a git repository to function.")
        msg.setInformativeText(str(e))
        msg.exec()
        sys.exit(1)


if __name__ == "__main__":
    main()
