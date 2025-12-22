#!/usr/bin/env python3
"""
Forge - AI-assisted development environment
"""

import argparse
import sys

from PySide6.QtWidgets import QApplication

from forge.ui.main_window import MainWindow


def parse_args() -> argparse.Namespace:
    """Parse command line arguments"""
    parser = argparse.ArgumentParser(
        prog="forge",
        description="Forge - AI-assisted development environment",
    )
    parser.add_argument(
        "files",
        nargs="*",
        help="Files to open on startup",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    app = QApplication(sys.argv)
    app.setApplicationName("Forge")
    app.setOrganizationName("Forge")

    try:
        window = MainWindow(initial_files=args.files)
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
