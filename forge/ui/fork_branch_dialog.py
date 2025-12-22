"""Dialog for forking a branch with options."""

from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QLabel,
    QLineEdit,
    QVBoxLayout,
)


class ForkBranchDialog(QDialog):
    """Dialog for forking a branch with session options."""

    def __init__(self, source_branch: str, parent: object = None) -> None:
        super().__init__(parent)  # type: ignore[arg-type]
        self.setWindowTitle("Fork Branch")
        self.setMinimumWidth(350)

        layout = QVBoxLayout(self)

        # Branch name input
        layout.addWidget(QLabel(f"New branch name (forking from {source_branch}):"))
        self.name_edit = QLineEdit(f"{source_branch}-fork")
        self.name_edit.selectAll()
        layout.addWidget(self.name_edit)

        # Session checkbox
        self.include_session = QCheckBox("Include conversation history")
        self.include_session.setChecked(True)
        self.include_session.setToolTip(
            "If checked, the new branch will have a copy of the AI chat history.\n"
            "If unchecked, the new branch starts with a fresh conversation."
        )
        layout.addWidget(self.include_session)

        # Buttons
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.name_edit.setFocus()

    def get_branch_name(self) -> str:
        """Get the entered branch name."""
        return self.name_edit.text().strip()

    def should_include_session(self) -> bool:
        """Check if session should be copied."""
        return self.include_session.isChecked()
