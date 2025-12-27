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
    except ValueError:
        # Not in a git repository - offer to create initial commit
        import os
        import subprocess

        from PySide6.QtWidgets import QMessageBox

        msg = QMessageBox()
        msg.setIcon(QMessageBox.Icon.Question)
        msg.setWindowTitle("Git Repository Required")
        msg.setText("Forge requires a git repository to function.")
        msg.setInformativeText(
            "Would you like to initialize a git repository and create an initial commit?"
        )
        msg.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        msg.setDefaultButton(QMessageBox.StandardButton.Yes)

        if msg.exec() == QMessageBox.StandardButton.Yes:
            cwd = os.getcwd()
            try:
                # Initialize git repo
                subprocess.run(["git", "init"], cwd=cwd, check=True, capture_output=True)
                # Add all files (if any)
                subprocess.run(["git", "add", "."], cwd=cwd, check=True, capture_output=True)
                # Check if there's anything to commit
                status = subprocess.run(
                    ["git", "status", "--porcelain"],
                    cwd=cwd,
                    check=True,
                    capture_output=True,
                    text=True,
                )
                if status.stdout.strip():
                    # There are files to commit
                    subprocess.run(
                        ["git", "commit", "-m", "Initial commit"],
                        cwd=cwd,
                        check=True,
                        capture_output=True,
                    )
                else:
                    # Empty directory - create empty initial commit
                    subprocess.run(
                        ["git", "commit", "--allow-empty", "-m", "Initial commit"],
                        cwd=cwd,
                        check=True,
                        capture_output=True,
                    )
                # Retry opening the window
                window = MainWindow(initial_files=args.files)
                window.show()
                sys.exit(app.exec())
            except subprocess.CalledProcessError as git_error:
                error_msg = QMessageBox()
                error_msg.setIcon(QMessageBox.Icon.Critical)
                error_msg.setWindowTitle("Git Initialization Failed")
                error_msg.setText("Failed to initialize git repository.")
                error_msg.setInformativeText(str(git_error))
                error_msg.exec()
                sys.exit(1)
        else:
            sys.exit(1)


if __name__ == "__main__":
    main()
