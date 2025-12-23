"""
Settings dialog for Forge
"""

from typing import TYPE_CHECKING  # noqa: I001

from PySide6.QtCore import QEvent, QObject, Qt, QThread, Signal
from PySide6.QtGui import QColor, QIcon, QKeySequence, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QKeySequenceEdit,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from forge.llm.client import LLMClient
from forge.ui.model_picker_dialog import ModelPickerPopup

if TYPE_CHECKING:
    from forge.config.settings import Settings  # noqa: I001


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
        self._populate_keybindings()

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

        # Keybindings Tab
        keybindings_tab = self._create_keybindings_tab()
        tabs.addTab(keybindings_tab, "Keybindings")

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

        # API Key with inline eye toggle
        self.api_key_input = QLineEdit()
        self.api_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key_input.setPlaceholderText(
            "Enter OpenRouter API key or set OPENROUTER_API_KEY env var"
        )

        # Add eye icon as inline action
        self._api_key_visible = False
        self.api_key_eye_action = self.api_key_input.addAction(
            self.style().standardIcon(self.style().StandardPixmap.SP_TitleBarContextHelpButton),
            QLineEdit.ActionPosition.TrailingPosition,
        )
        self._update_eye_icon()
        self.api_key_eye_action.triggered.connect(self._toggle_api_key_visibility)

        layout.addRow("API Key:", self.api_key_input)

        # Model selection with popup picker (click to open)
        self.model_input = QLineEdit()
        self.model_input.setReadOnly(True)
        self.model_input.setPlaceholderText("Loading models...")
        self.model_input.setCursor(Qt.CursorShape.PointingHandCursor)
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

        # Connect click events using event filter or subclass approach
        self.model_input.installEventFilter(self)

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

        # Summarization model with popup picker (click to open)
        self.summarization_model_input = QLineEdit()
        self.summarization_model_input.setReadOnly(True)
        self.summarization_model_input.setPlaceholderText("Loading models...")
        self.summarization_model_input.setCursor(Qt.CursorShape.PointingHandCursor)
        self._summarization_model_picker_enabled = False

        layout.addRow("Summarization Model:", self.summarization_model_input)

        # Info
        info = QLabel(
            "Used for commit messages, file summaries, code completion,\n"
            "and 'Ask' queries. A smaller/cheaper model is recommended."
        )
        info.setWordWrap(True)
        info.setStyleSheet("color: #666; font-size: 10px;")
        layout.addRow("", info)

        # Connect click events using event filter
        self.summarization_model_input.installEventFilter(self)

        return widget

    def _create_keybindings_tab(self) -> QWidget:
        """Create keybindings settings tab"""
        widget = QWidget()
        layout = QVBoxLayout(widget)

        # Instructions
        info = QLabel(
            "Click on a shortcut to change it. Press Escape to clear.\n"
            "Changes take effect after saving and restarting."
        )
        info.setStyleSheet("color: #666; font-size: 11px; margin-bottom: 8px;")
        layout.addWidget(info)

        # Table for keybindings
        self.keybindings_table = QTableWidget()
        self.keybindings_table.setColumnCount(3)
        self.keybindings_table.setHorizontalHeaderLabels(["Action", "Shortcut", "Default"])
        self.keybindings_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        self.keybindings_table.horizontalHeader().setSectionResizeMode(
            1, QHeaderView.ResizeMode.ResizeToContents
        )
        self.keybindings_table.horizontalHeader().setSectionResizeMode(
            2, QHeaderView.ResizeMode.ResizeToContents
        )
        self.keybindings_table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.keybindings_table.verticalHeader().setVisible(False)

        layout.addWidget(self.keybindings_table)

        # Reset button
        reset_btn = QPushButton("Reset All to Defaults")
        reset_btn.clicked.connect(self._reset_keybindings)
        layout.addWidget(reset_btn)

        # Store keybinding edits for later access
        self._keybinding_edits: dict[str, QKeySequenceEdit] = {}

        return widget

    def _populate_keybindings(self) -> None:
        """Populate the keybindings table from ActionRegistry"""
        # Get action registry from parent (MainWindow)
        main_window = self.parent()
        if not hasattr(main_window, "action_registry"):
            return

        registry = main_window.action_registry
        actions = registry.get_all()

        # Sort by category then name
        actions.sort(key=lambda a: (a.category, a.name))

        self.keybindings_table.setRowCount(len(actions))

        for row, action in enumerate(actions):
            # Action name (with category)
            name_item = QTableWidgetItem(f"{action.name}")
            name_item.setData(Qt.ItemDataRole.UserRole, action.id)
            name_item.setFlags(name_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            name_item.setToolTip(f"[{action.category}] {action.id}")
            self.keybindings_table.setItem(row, 0, name_item)

            # Current shortcut (editable)
            effective_shortcut = registry.get_effective_shortcut(action.id)
            shortcut_edit = QKeySequenceEdit(QKeySequence(effective_shortcut))
            shortcut_edit.setProperty("action_id", action.id)
            self.keybindings_table.setCellWidget(row, 1, shortcut_edit)
            self._keybinding_edits[action.id] = shortcut_edit

            # Default shortcut (read-only)
            default_item = QTableWidgetItem(action.shortcut or "(none)")
            default_item.setFlags(default_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            default_item.setForeground(Qt.GlobalColor.gray)
            self.keybindings_table.setItem(row, 2, default_item)

    def _reset_keybindings(self) -> None:
        """Reset all keybindings to defaults"""
        main_window = self.parent()
        if not hasattr(main_window, "action_registry"):
            return

        registry = main_window.action_registry

        for action_id, edit in self._keybinding_edits.items():
            action = registry.get(action_id)
            if action:
                edit.setKeySequence(QKeySequence(action.shortcut))

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802
        """Handle click events on model input fields"""
        if event.type() == QEvent.Type.MouseButtonPress:
            if obj == self.model_input:
                self._show_model_picker()
                return True
            elif obj == self.summarization_model_input:
                self._show_summarization_model_picker()
                return True
        return super().eventFilter(obj, event)

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
        self._summarization_model_picker_enabled = True

        # Set saved values
        saved_model = getattr(self, "_saved_model", "anthropic/claude-3.5-sonnet")
        saved_summarization_model = getattr(
            self, "_saved_summarization_model", "anthropic/claude-3-haiku"
        )

        self.model_input.setText(saved_model)
        self.summarization_model_input.setText(saved_summarization_model)

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
        self._summarization_model_picker_enabled = True

        # Set saved values
        saved_model = getattr(self, "_saved_model", "anthropic/claude-3.5-sonnet")
        saved_summarization_model = getattr(
            self, "_saved_summarization_model", "anthropic/claude-3-haiku"
        )

        self.model_input.setText(saved_model)
        self.summarization_model_input.setText(saved_summarization_model)

    def _show_model_picker(self) -> None:
        """Show the model picker popup"""
        if not getattr(self, "_model_picker_enabled", False):
            return

        models = getattr(self, "_available_models", [])

        picker = ModelPickerPopup(models, self.model_input.text(), self)
        picker.model_selected.connect(self._on_model_selected)

        # Position below and aligned to left of the input field
        input_pos = self.model_input.mapToGlobal(self.model_input.rect().bottomLeft())
        picker.show_at(input_pos)

    def _on_model_selected(self, model: str) -> None:
        """Handle model selection from picker"""
        self.model_input.setText(model)

    def _show_summarization_model_picker(self) -> None:
        """Show the summarization model picker popup"""
        if not getattr(self, "_summarization_model_picker_enabled", False):
            return

        models = getattr(self, "_available_models", [])

        picker = ModelPickerPopup(models, self.summarization_model_input.text(), self)
        picker.model_selected.connect(self._on_summarization_model_selected)

        # Position below and aligned to left of the input field
        input_pos = self.summarization_model_input.mapToGlobal(
            self.summarization_model_input.rect().bottomLeft()
        )
        picker.show_at(input_pos)

    def _on_summarization_model_selected(self, model: str) -> None:
        """Handle summarization model selection from picker"""
        self.summarization_model_input.setText(model)

    def _toggle_api_key_visibility(self) -> None:
        """Toggle API key visibility between hidden and visible"""
        self._api_key_visible = not self._api_key_visible
        if self._api_key_visible:
            self.api_key_input.setEchoMode(QLineEdit.EchoMode.Normal)
        else:
            self.api_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self._update_eye_icon()

    def _create_eye_icon(self, crossed: bool = False) -> QIcon:
        """Create an eye icon for visibility toggle"""
        size = 16
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)

        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # Gray color for the icon
        gray = QColor(128, 128, 128)
        pen = QPen(gray, 1.5)
        painter.setPen(pen)

        # Draw eye shape (almond/lens shape)
        cx, cy = size // 2, size // 2
        # Eye outline using arcs
        from PySide6.QtCore import QPointF
        from PySide6.QtGui import QPainterPath

        path = QPainterPath()
        # Left point, curve up to right, curve down back to left
        path.moveTo(2, cy)
        path.quadTo(cx, cy - 5, size - 2, cy)  # Top curve
        path.quadTo(cx, cy + 5, 2, cy)  # Bottom curve
        painter.drawPath(path)

        # Draw pupil (circle in center)
        painter.setBrush(gray)
        painter.drawEllipse(QPointF(cx, cy), 2.5, 2.5)

        # Draw crossed line if hidden
        if crossed:
            pen = QPen(gray, 1.5)
            painter.setPen(pen)
            painter.drawLine(3, size - 3, size - 3, 3)

        painter.end()
        return QIcon(pixmap)

    def _update_eye_icon(self) -> None:
        """Update the eye icon based on visibility state"""
        if self._api_key_visible:
            # Eye with slash - click to hide
            self.api_key_eye_action.setIcon(self._create_eye_icon(crossed=True))
            self.api_key_eye_action.setToolTip("Hide API key")
        else:
            # Open eye - click to reveal
            self.api_key_eye_action.setIcon(self._create_eye_icon(crossed=False))
            self.api_key_eye_action.setToolTip("Reveal API key")

    def _load_settings(self) -> None:
        """Load current settings into UI"""
        # LLM settings
        self.api_key_input.setText(self.settings.get("llm.api_key", ""))
        # Store model values - they'll be applied when models are loaded
        self._saved_model = self.settings.get("llm.model", "anthropic/claude-3.5-sonnet")
        self._saved_summarization_model = self.settings.get(
            "llm.summarization_model", "anthropic/claude-3-haiku"
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
        self.summarization_model_input.setText(self._saved_summarization_model)

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
        self.settings.set("llm.summarization_model", self.summarization_model_input.text())

        # Keybindings - collect custom shortcuts
        keybindings: dict[str, str] = {}
        main_window = self.parent()
        if hasattr(main_window, "action_registry"):
            registry = main_window.action_registry
            for action_id, edit in self._keybinding_edits.items():
                action = registry.get(action_id)
                if action:
                    new_shortcut = edit.keySequence().toString()
                    # Only save if different from default
                    if new_shortcut != action.shortcut:
                        keybindings[action_id] = new_shortcut

        self.settings.set("keybindings", keybindings)

        # Save to file
        self.settings.save()

        self.accept()
