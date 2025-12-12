"""
Welcome/dashboard widget shown on startup
"""

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..git_backend.repository import ForgeRepository


class WelcomeWidget(QWidget):
    """Welcome screen with repo stats and quick actions"""

    new_session_requested = Signal()
    open_file_requested = Signal(str)
    open_session_requested = Signal(str)  # Emits session_id

    def __init__(self, repo: ForgeRepository) -> None:
        super().__init__()
        self.repo = repo
        self._setup_ui()

    def _setup_ui(self) -> None:
        """Setup the welcome screen UI"""
        # Main scroll area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)

        # Content widget
        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(40, 40, 40, 40)
        layout.setSpacing(30)

        # Title
        title = QLabel("🔨 Welcome to Forge")
        title.setStyleSheet("font-size: 32px; font-weight: bold; color: #2c3e50;")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(title)

        # Subtitle
        subtitle = QLabel("AI-Assisted Development Environment")
        subtitle.setStyleSheet("font-size: 16px; color: #7f8c8d;")
        subtitle.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(subtitle)

        layout.addSpacing(20)

        # New Session Button (prominent)
        new_session_btn = QPushButton("✨ Start New AI Session")
        new_session_btn.setStyleSheet(
            """
            QPushButton {
                background-color: #3498db;
                color: white;
                font-size: 18px;
                font-weight: bold;
                padding: 20px 40px;
                border-radius: 8px;
                border: none;
            }
            QPushButton:hover {
                background-color: #2980b9;
            }
            QPushButton:pressed {
                background-color: #21618c;
            }
            """
        )
        new_session_btn.clicked.connect(self.new_session_requested.emit)
        new_session_btn.setMaximumWidth(400)

        btn_container = QWidget()
        btn_layout = QHBoxLayout(btn_container)
        btn_layout.addStretch()
        btn_layout.addWidget(new_session_btn)
        btn_layout.addStretch()
        layout.addWidget(btn_container)

        layout.addSpacing(20)

        # Existing Sessions Section
        sessions_frame = self._create_sessions_section()
        layout.addWidget(sessions_frame)

        # Quick Access Section
        files_frame = self._create_files_section()
        layout.addWidget(files_frame)

        # Repository Stats Section
        stats_frame = self._create_stats_section()
        layout.addWidget(stats_frame)

        # Tips Section
        tips_frame = self._create_tips_section()
        layout.addWidget(tips_frame)

        layout.addStretch()

        scroll.setWidget(content)

        # Main layout
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.addWidget(scroll)

    def _create_stats_section(self) -> QFrame:
        """Create repository statistics section"""
        frame = QFrame()
        frame.setFrameStyle(QFrame.Shape.StyledPanel)
        frame.setStyleSheet(
            "QFrame { background-color: #ecf0f1; border-radius: 8px; padding: 20px; }"
        )

        layout = QVBoxLayout(frame)

        # Section title
        title = QLabel("📊 Repository Statistics")
        title.setStyleSheet("font-size: 18px; font-weight: bold; color: #2c3e50;")
        layout.addWidget(title)

        layout.addSpacing(10)

        # Get stats
        try:
            all_files = self.repo.get_all_files()
            file_count = len(all_files)

            # Count by extension
            extensions: dict[str, int] = {}
            for filepath in all_files:
                ext = Path(filepath).suffix or "(no extension)"
                extensions[ext] = extensions.get(ext, 0) + 1

            # Current branch
            current_branch = self.repo.repo.head.shorthand

            # Stats grid
            stats_layout = QVBoxLayout()
            stats_layout.setSpacing(8)

            stats_layout.addWidget(self._create_stat_row("📁 Total Files:", str(file_count)))
            stats_layout.addWidget(self._create_stat_row("🌿 Current Branch:", current_branch))

            # Top file types
            if extensions:
                top_exts = sorted(extensions.items(), key=lambda x: x[1], reverse=True)[:5]
                ext_text = ", ".join(f"{ext} ({count})" for ext, count in top_exts)
                stats_layout.addWidget(self._create_stat_row("📝 File Types:", ext_text))

            layout.addLayout(stats_layout)

        except Exception as e:
            error_label = QLabel(f"Error loading stats: {e}")
            error_label.setStyleSheet("color: #e74c3c;")
            layout.addWidget(error_label)

        return frame

    def _create_stat_row(self, label: str, value: str) -> QWidget:
        """Create a single stat row"""
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)

        label_widget = QLabel(label)
        label_widget.setStyleSheet("font-weight: bold; color: #34495e;")

        value_widget = QLabel(value)
        value_widget.setStyleSheet("color: #7f8c8d;")
        value_widget.setWordWrap(True)

        layout.addWidget(label_widget)
        layout.addWidget(value_widget, 1)

        return widget

    def _create_files_section(self) -> QFrame:
        """Create quick access to top-level files section"""
        frame = QFrame()
        frame.setFrameStyle(QFrame.Shape.StyledPanel)
        frame.setStyleSheet(
            "QFrame { background-color: #ecf0f1; border-radius: 8px; padding: 20px; }"
        )

        layout = QVBoxLayout(frame)

        # Section title
        title = QLabel("📂 Quick Access - Top Level Files")
        title.setStyleSheet("font-size: 18px; font-weight: bold; color: #2c3e50;")
        layout.addWidget(title)

        layout.addSpacing(10)

        # Get top-level files
        try:
            all_files = self.repo.get_all_files()
            top_level_files = [f for f in all_files if "/" not in f]

            # Sort by common importance
            priority_files = ["README.md", "main.py", "pyproject.toml", "requirements.txt"]
            sorted_files = []
            for pf in priority_files:
                if pf in top_level_files:
                    sorted_files.append(pf)
            sorted_files.extend(sorted(f for f in top_level_files if f not in sorted_files))

            if sorted_files:
                files_layout = QVBoxLayout()
                files_layout.setSpacing(5)

                for filepath in sorted_files[:10]:  # Show max 10
                    file_btn = QPushButton(f"📄 {filepath}")
                    file_btn.setStyleSheet(
                        """
                        QPushButton {
                            text-align: left;
                            padding: 8px 12px;
                            background-color: white;
                            border: 1px solid #bdc3c7;
                            border-radius: 4px;
                        }
                        QPushButton:hover {
                            background-color: #3498db;
                            color: white;
                            border-color: #2980b9;
                        }
                        """
                    )
                    file_btn.clicked.connect(
                        lambda checked, f=filepath: self.open_file_requested.emit(f)
                    )
                    files_layout.addWidget(file_btn)

                layout.addLayout(files_layout)
            else:
                no_files = QLabel("No top-level files found")
                no_files.setStyleSheet("color: #7f8c8d; font-style: italic;")
                layout.addWidget(no_files)

        except Exception as e:
            error_label = QLabel(f"Error loading files: {e}")
            error_label.setStyleSheet("color: #e74c3c;")
            layout.addWidget(error_label)

        return frame

    def _create_sessions_section(self) -> QFrame:
        """Create existing sessions section"""
        frame = QFrame()
        frame.setFrameStyle(QFrame.Shape.StyledPanel)
        frame.setStyleSheet(
            "QFrame { background-color: #ecf0f1; border-radius: 8px; padding: 20px; }"
        )

        layout = QVBoxLayout(frame)

        # Section title
        title = QLabel("🤖 Existing AI Sessions")
        title.setStyleSheet("font-size: 18px; font-weight: bold; color: #2c3e50;")
        layout.addWidget(title)

        layout.addSpacing(10)

        # Get session branches from git
        try:
            session_branches = [
                name for name in self.repo.repo.branches if name.startswith("forge/session/")
            ]

            if session_branches:
                sessions_layout = QVBoxLayout()
                sessions_layout.setSpacing(5)

                for branch_name in sorted(session_branches):
                    # Extract session ID from branch name
                    session_id = branch_name.replace("forge/session/", "")

                    # Create button for session
                    session_btn = QPushButton(f"📋 {session_id[:8]}... ({branch_name})")
                    session_btn.setStyleSheet(
                        """
                        QPushButton {
                            text-align: left;
                            padding: 8px 12px;
                            background-color: white;
                            border: 1px solid #bdc3c7;
                            border-radius: 4px;
                        }
                        QPushButton:hover {
                            background-color: #3498db;
                            color: white;
                            border-color: #2980b9;
                        }
                        """
                    )
                    # Emit signal to open this session
                    session_btn.clicked.connect(
                        lambda checked, sid=session_id: self.open_session_requested.emit(sid)
                    )
                    sessions_layout.addWidget(session_btn)

                layout.addLayout(sessions_layout)
            else:
                no_sessions = QLabel("No existing sessions found. Start a new one!")
                no_sessions.setStyleSheet("color: #7f8c8d; font-style: italic;")
                layout.addWidget(no_sessions)

        except Exception as e:
            error_label = QLabel(f"Error loading sessions: {e}")
            error_label.setStyleSheet("color: #e74c3c;")
            layout.addWidget(error_label)

        return frame

    def _create_tips_section(self) -> QFrame:
        """Create tips and documentation section"""
        frame = QFrame()
        frame.setFrameStyle(QFrame.Shape.StyledPanel)
        frame.setStyleSheet(
            "QFrame { background-color: #e8f8f5; border-radius: 8px; padding: 20px; }"
        )

        layout = QVBoxLayout(frame)

        # Section title
        title = QLabel("💡 Quick Tips")
        title.setStyleSheet("font-size: 18px; font-weight: bold; color: #16a085;")
        layout.addWidget(title)

        layout.addSpacing(10)

        tips = [
            "🤖 Each AI session runs on its own git branch - your work is always safe",
            "✅ Review and approve tools before they run - security first!",
            "📝 All AI changes are committed to git - full audit trail and time travel",
            "🔧 Use Ctrl+O to open files, Ctrl+N for new AI sessions",
            "💾 Changes are committed after each AI turn - one atomic commit per response",
        ]

        for tip in tips:
            tip_label = QLabel(tip)
            tip_label.setWordWrap(True)
            tip_label.setStyleSheet("color: #2c3e50; padding: 5px 0;")
            layout.addWidget(tip_label)

        return frame
