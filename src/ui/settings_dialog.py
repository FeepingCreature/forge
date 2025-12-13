"""
Settings dialog for Forge
"""

from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QObject, Qt, QThread, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .model_picker_dialog import ModelPickerPopup

if TYPE_CHECKING:
    from ..config.settings import Settings

from ..llm.client import LLMClient


class ModelFetchWorker(QObject):
    """Worker to fetch models in background thread"""

    finished = Signal(list)  # Emits list of model dicts
    error = Signal(str)  # Emits error message

    def __init__(self, api_key: str) -> None:
        super().__init__()
        self.api_key = api_key

    def run(self) -> None:
        """Fetch models from OpenRouter"""
        try:
            client = LLMClient(self.api_key)
            models = client.get_available_models()
            self.finished.emit(models)
        except Exception as e:
            self.error.emit(str(e))


class SettingsDialog(QDialog):
    """Settings dialog window"""

    def __init__(self, settings: "Settings", parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle("Forge Settings")
        self.setMinimumWidth(600)
        self.setMinimumHeight(400)

        # Thread for fetching models
        self.model_thread: QThread | None = None
        self.model_worker: ModelFetchWorker | None = None

        self._setup_ui()
        self._load_settings()
        self._fetch_models()

    def _setup_ui(self) -> None:
        """Setup the dialog UI"""
        layout = QVBoxLayout(self)

        # Tab widget for different setting categories
        tabs = QTabWidget()

        # LLM Settings Tab
        llm_tab = self._create_llm_tab()
        tabs.addTab(llm_tab, "LLM")

        # Editor Settings Tab
        editor_tab = self._create_editor_tab()
        tabs.addTab(editor_tab, "Editor")

        # Git Settings Tab
        git_tab = self._create_git_tab()
        tabs.addTab(git_tab, "Git")

        layout.addWidget(tabs)

        # Buttons
        button_layout = QHBoxLayout()
        button_layout.addStretch()

        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)

        save_btn = QPushButton("Save")
        save_btn.clicked.connect(self._save_and_close)
        save_btn.setDefault(True)

        button_layout.addWidget(cancel_btn)
        button_layout.addWidget(save_btn)

        layout.addLayout(button_layout)

    def _create_llm_tab(self) -> QWidget:
        """Create LLM settings tab"""
        widget = QWidget()
        layout = QFormLayout(widget)

        # API Key
        self.api_key_input = QLineEdit()
        self.api_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key_input.setPlaceholderText(
            "Enter OpenRouter API key or set OPENROUTER_API_KEY env var"
        )
        layout.addRow("API Key:", self.api_key_input)

        # Model selection with popup picker (click to open)
        self.model_input = QLineEdit()
        self.model_input.setReadOnly(True)
        self.model_input.setPlaceholderText("Loading models...")
        self.model_input.setCursor(Qt.CursorShape.PointingHandCursor)
        self.model_input.mousePressEvent = lambda e: self._show_model_picker()
        self._model_picker_enabled = False

        layout.addRow("Model:", self.model_input)

        # Base URL
        self.base_url_input = QLineEdit()
        self.base_url_input.setPlaceholderText("https://openrouter.ai/api/v1")
        layout.addRow("Base URL:", self.base_url_input)

        # Info label
        info = QLabel(
            "Note: API key can also be set via OPENROUTER_API_KEY environment variable.\n"
            "If both are set, the environment variable takes precedence."
        )
        info.setWordWrap(True)
        info.setStyleSheet("color: #666; font-size: 10px;")
        layout.addRow("", info)

        return widget

    def _create_editor_tab(self) -> QWidget:
        """Create editor settings tab"""
        widget = QWidget()
        layout = QFormLayout(widget)

        # Font size
        self.font_size_input = QSpinBox()
        self.font_size_input.setRange(8, 24)
        layout.addRow("Font Size:", self.font_size_input)

        # Tab width
        self.tab_width_input = QSpinBox()
        self.tab_width_input.setRange(2, 8)
        layout.addRow("Tab Width:", self.tab_width_input)

        # Show line numbers
        self.show_line_numbers_input = QCheckBox()
        layout.addRow("Show Line Numbers:", self.show_line_numbers_input)

        # Highlight current line
        self.highlight_line_input = QCheckBox()
        layout.addRow("Highlight Current Line:", self.highlight_line_input)

        return widget

    def _create_git_tab(self) -> QWidget:
        """Create git settings tab"""
        widget = QWidget()
        layout = QFormLayout(widget)

        # Auto-commit
        self.auto_commit_input = QCheckBox()
        layout.addRow("Auto-commit AI changes:", self.auto_commit_input)

        # Commit message model with popup picker (click to open)
        self.commit_model_input = QLineEdit()
        self.commit_model_input.setReadOnly(True)
        self.commit_model_input.setPlaceholderText("Loading models...")
        self.commit_model_input.setCursor(Qt.CursorShape.PointingHandCursor)
        self.commit_model_input.mousePressEvent = lambda e: self._show_commit_model_picker()
        self._commit_model_picker_enabled = False

        layout.addRow("Commit Message Model:", self.commit_model_input)

        # Info
        info = QLabel(
            "The commit message model is used to generate commit messages\n"
            "for AI changes. A smaller/cheaper model is recommended."
        )
        info.setWordWrap(True)
        info.setStyleSheet("color: #666; font-size: 10px;")
        layout.addRow("", info)

        return widget

    def _fetch_models(self) -> None:
        """Fetch available models from OpenRouter in background"""
        api_key = self.settings.get_api_key()
        if not api_key:
            # No API key, use fallback list
            self._populate_fallback_models()
            return

        self.model_thread = QThread()
        self.model_worker = ModelFetchWorker(api_key)
        self.model_worker.moveToThread(self.model_thread)

        # Connect signals
        self.model_worker.finished.connect(self._on_models_fetched)
        self.model_worker.error.connect(self._on_models_error)
        self.model_thread.started.connect(self.model_worker.run)

        self.model_thread.start()

    def _on_models_fetched(self, models: list[dict[str, str]]) -> None:
        """Handle successful model fetch"""
        # Clean up thread
        if self.model_thread:
            self.model_thread.quit()
            self.model_thread.wait()
            self.model_thread = None
            self.model_worker = None

        # Store model list for picker dialog
        self._available_models = [m.get("id", "") for m in models if m.get("id")]

        # Enable clicking on model inputs
        self._model_picker_enabled = True
        self._commit_model_picker_enabled = True

        # Set saved values
        saved_model = getattr(self, "_saved_model", "anthropic/claude-3.5-sonnet")
        saved_commit_model = getattr(self, "_saved_commit_model", "anthropic/claude-3-haiku")

        self.model_input.setText(saved_model)
        self.commit_model_input.setText(saved_commit_model)

    def _on_models_error(self, error_msg: str) -> None:
        """Handle model fetch error"""
        # Clean up thread
        if self.model_thread:
            self.model_thread.quit()
            self.model_thread.wait()
            self.model_thread = None
            self.model_worker = None

        print(f"Failed to fetch models: {error_msg}")
        self._populate_fallback_models()

    def _populate_fallback_models(self) -> None:
        """Populate with fallback model list when API is unavailable"""
        self._available_models = [
            "anthropic/claude-3.5-sonnet",
            "anthropic/claude-3-opus",
            "anthropic/claude-3-haiku",
            "openai/gpt-4-turbo",
            "openai/gpt-4",
            "openai/gpt-3.5-turbo",
        ]

        # Enable clicking on model inputs
        self._model_picker_enabled = True
        self._commit_model_picker_enabled = True

        # Set saved values
        saved_model = getattr(self, "_saved_model", "anthropic/claude-3.5-sonnet")
        saved_commit_model = getattr(self, "_saved_commit_model", "anthropic/claude-3-haiku")

        self.model_input.setText(saved_model)
        self.commit_model_input.setText(saved_commit_model)

    def _show_model_picker(self) -> None:
        """Show the model picker popup"""
        if not getattr(self, "_model_picker_enabled", False):
            return

        models = getattr(self, "_available_models", [])

        picker = ModelPickerPopup(models, self.model_input.text(), self)
        picker.model_selected.connect(self._on_model_selected)

        # Position below and aligned to left of the input field
        input_pos = self.model_input.mapToGlobal(
            self.model_input.rect().bottomLeft()
        )
        picker.showAt(input_pos)

    def _on_model_selected(self, model: str) -> None:
        """Handle model selection from picker"""
        self.model_input.setText(model)

    def _show_commit_model_picker(self) -> None:
        """Show the commit model picker popup"""
        if not getattr(self, "_commit_model_picker_enabled", False):
            return

        models = getattr(self, "_available_models", [])

        picker = ModelPickerPopup(models, self.commit_model_input.text(), self)
        picker.model_selected.connect(self._on_commit_model_selected)

        # Position below and aligned to left of the input field
        input_pos = self.commit_model_input.mapToGlobal(
            self.commit_model_input.rect().bottomLeft()
        )
        picker.showAt(input_pos)

    def _on_commit_model_selected(self, model: str) -> None:
        """Handle commit model selection from picker"""
        self.commit_model_input.setText(model)

    def _load_settings(self) -> None:
        """Load current settings into UI"""
        # LLM settings
        self.api_key_input.setText(self.settings.get("llm.api_key", ""))
        # Store model values - they'll be applied when models are loaded
        self._saved_model = self.settings.get("llm.model", "anthropic/claude-3.5-sonnet")
        self._saved_commit_model = self.settings.get(
            "git.commit_message_model", "anthropic/claude-3-haiku"
        )
        self.model_input.setText(self._saved_model)
        self.base_url_input.setText(self.settings.get("llm.base_url", ""))

        # Editor settings
        self.font_size_input.setValue(self.settings.get("editor.font_size", 10))
        self.tab_width_input.setValue(self.settings.get("editor.tab_width", 4))
        self.show_line_numbers_input.setChecked(self.settings.get("editor.show_line_numbers", True))
        self.highlight_line_input.setChecked(
            self.settings.get("editor.highlight_current_line", True)
        )

        # Git settings
        self.auto_commit_input.setChecked(self.settings.get("git.auto_commit", False))
        self.commit_model_input.setText(self._saved_commit_model)

    def _save_and_close(self) -> None:
        """Save settings and close dialog"""
        # LLM settings
        self.settings.set("llm.api_key", self.api_key_input.text())
        self.settings.set("llm.model", self.model_input.text())
        self.settings.set("llm.base_url", self.base_url_input.text())

        # Editor settings
        self.settings.set("editor.font_size", self.font_size_input.value())
        self.settings.set("editor.tab_width", self.tab_width_input.value())
        self.settings.set("editor.show_line_numbers", self.show_line_numbers_input.isChecked())
        self.settings.set("editor.highlight_current_line", self.highlight_line_input.isChecked())

        # Git settings
        self.settings.set("git.auto_commit", self.auto_commit_input.isChecked())
        self.settings.set("git.commit_message_model", self.commit_model_input.text())

        # Save to file
        self.settings.save()

        self.accept()

