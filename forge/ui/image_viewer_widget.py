"""
ImageViewerWidget - minimal read-only viewer for image files in a tab.

Always displays the full-quality image - this is pure UI, unrelated to the
vision context mechanism (which controls what the model sees) or output
embedding (which controls what gets replayed to the model forever). See
IMAGE_TODO.md section 3.
"""

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap, QResizeEvent
from PySide6.QtWidgets import QLabel, QScrollArea, QVBoxLayout, QWidget


class ImageViewerWidget(QWidget):
    """
    Read-only image viewer with a fit-to-window toggle.

    Loads the given bytes into a QPixmap once at construction (always full
    quality - no Pillow, no resizing here). toggle_fit_to_window() lets the
    user switch between fit-to-window and actual size (scrollable).
    """

    def __init__(self, filepath: str, data: bytes, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.filepath = filepath

        self._pixmap = QPixmap()
        self._pixmap.loadFromData(data)
        self._fit_to_window = True

        self._label = QLabel()
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self._scroll_area = QScrollArea()
        self._scroll_area.setWidget(self._label)
        self._scroll_area.setWidgetResizable(True)
        self._scroll_area.setAlignment(Qt.AlignmentFlag.AlignCenter)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._scroll_area)

        self._update_display()

    def _update_display(self) -> None:
        """Render the pixmap into the label, scaled to fit if enabled."""
        if self._pixmap.isNull():
            self._label.setText("(unable to load image)")
            return

        if self._fit_to_window:
            available = self._scroll_area.viewport().size()
            scaled = self._pixmap.scaled(
                available,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self._label.setPixmap(scaled)
        else:
            self._label.setPixmap(self._pixmap)

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        """Re-scale the displayed pixmap when the viewer is resized (fit mode only)."""
        super().resizeEvent(event)
        if self._fit_to_window:
            self._update_display()

    def toggle_fit_to_window(self) -> None:
        """Toggle between fit-to-window and actual-size (scrollable) display."""
        self._fit_to_window = not self._fit_to_window
        self._update_display()
