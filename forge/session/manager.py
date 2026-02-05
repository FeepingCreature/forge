"""
Session manager for coordinating AI turns and git commits
"""

import hashlib
import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

    from forge.config.settings import Settings
    from forge.vfs.work_in_progress import WorkInProgressVFS

from PySide6.QtCore import QObject, QThread, Signal

from forge.constants import SESSION_FILE
from forge.git_backend.commit_types import CommitType
from forge.git_backend.repository import ForgeRepository
from forge.llm.client import LLMClient
from forge.llm.request_log import REQUEST_LOG, RequestLogEntry
from forge.prompts.manager import PromptManager
from forge.tools.manager import ToolManager


class SessionManager(QObject):
    """Manages AI session lifecycle and git integration.

    Emits signals for context changes so UI can update without
    routing through the chat widget.
    """

    # Signals for context changes
    context_changed = Signal(set)  # Emitted when active_files changes (set of filepaths)
    context_stats_updated = Signal(dict)  # Emitted with token counts for status bar
    summaries_ready = Signal(dict)  # Emitted when repo summaries are ready (filepath -> summary)

    # Signals for summary generation progress
    summary_progress = Signal(int, int, str)  # current, total, filepath
    summary_finished = Signal(int)  # count of files summarized
    summary_error = Signal(str)  # error message

    # Signal for mid-turn commits (git graph needs to refresh)
    mid_turn_commit = Signal(str)  # commit_oid

    def __init__(self, repo: ForgeRepository, branch_name: str, settings: "Settings") -> None:
        super().__init__()
        self.branch_name = branch_name
        self.settings = settings

        # Tool manager owns the VFS - all file access goes through it
        self.tool_manager = ToolManager(repo, branch_name)

        # Keep repo reference only for commit operations (not file reading)
        self._repo = repo

        # Prompt manager for cache-optimized prompt construction
        # Pass tool schemas so inline tool documentation gets generated
        tool_schemas = self.tool_manager.discover_tools()
        self.prompt_manager = PromptManager(tool_schemas=tool_schemas)

        # Active files in context (tracked separately for persistence)
        self.active_files: set[str] = set()

        # Track if a mid-turn commit happened (for FOLLOW_UP logic)
        self._had_mid_turn_commit = False

        # Repository summaries cache (in-memory)
        self.repo_summaries: dict[str, str] = {}

        # XDG cache directory for persistent summary cache
        self.cache_dir = self._get_cache_dir()
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        # Summary generation state
        self._summaries_ready = False
        self._summary_thread: QThread | None = None
        self._summary_worker: Any = None  # SummaryWorker, but imported lazily to avoid cycles

        # Auto-start summary generation (session infrastructure, not UI concern)
        # This runs in background - UI can connect to signals for progress feedback
        self.start_summary_generation()

    @property
    def are_summaries_ready(self) -> bool:
        """Check if summaries have been generated."""
        return self._summaries_ready

    def is_on_checked_out_branch(self) -> bool:
        """Check if this session's branch is currently checked out in the working directory."""
        checked_out = self._repo.get_checked_out_branch()
        return checked_out == self.branch_name

    def is_workdir_clean(self) -> bool:
        """Check if the working directory is clean (no uncommitted changes)."""
        return self._repo.is_workdir_clean()

    def get_workdir_changes(self) -> dict[str, int]:
        """Get uncommitted working directory changes."""
        return self._repo.get_workdir_changes()

    def _get_cache_dir(self) -> Path:
        """Get XDG cache directory for repository summaries"""
        xdg_cache = Path.home() / ".cache"
        if "XDG_CACHE_HOME" in __import__("os").environ:
            xdg_cache = Path(__import__("os").environ["XDG_CACHE_HOME"])
        return xdg_cache / "forge" / "summaries"

    def _get_cache_key(self, filepath: str, blob_oid: str) -> str:
        """Generate cache key from filepath and blob OID (content hash)"""
        # Use hash to keep filename reasonable length
        # blob_oid is the content hash, so same content = same cache key
        key_str = f"{blob_oid}:{filepath}"
        return hashlib.sha256(key_str.encode()).hexdigest()

    def _get_cached_summary(self, filepath: str, blob_oid: str) -> str | None:
        """Get cached summary for a file with a specific content hash"""
        cache_key = self._get_cache_key(filepath, blob_oid)
        cache_file = self.cache_dir / cache_key

        if cache_file.exists():
            return cache_file.read_text()
        return None

    def _cache_summary(self, filepath: str, blob_oid: str, summary: str) -> None:
        """Cache a summary for a file with a specific content hash"""
        cache_key = self._get_cache_key(filepath, blob_oid)
        cache_file = self.cache_dir / cache_key
        cache_file.write_text(summary)

    def _build_summary_prompt(self, filepath: str, content: str) -> str:
        """Build the prompt for generating a file summary"""
        return f"""Summarize this file's public interfaces for codebase navigation.

File: {filepath}

```
{content}
```

First, decide: is this CODE (Python, JS, etc. with importable classes/functions) or DATA (config, docs, markdown, licenses, etc)?

If CODE: list public classes/functions/constants as terse bullets (skip _ prefixed, under 80 chars each).
If DATA (including .md files): just output "—" (the filename alone is enough context for navigation).

Think about what category this file is, then put ONLY the final bullets or "—" inside <summary></summary> tags. Nothing else inside the tags."""

    # Binary/non-summarizable file extensions
    _SKIP_EXTENSIONS = {
        # Images
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".bmp",
        ".ico",
        ".svg",
        ".webp",
        # Fonts
        ".ttf",
        ".otf",
        ".woff",
        ".woff2",
        ".eot",
        # Audio/video
        ".mp3",
        ".mp4",
        ".wav",
        ".ogg",
        ".webm",
        ".avi",
        ".mov",
        # Archives
        ".zip",
        ".tar",
        ".gz",
        ".bz2",
        ".xz",
        ".7z",
        ".rar",
        # Binaries
        ".exe",
        ".dll",
        ".so",
        ".dylib",
        ".bin",
        ".dat",
        # Other
        ".pdf",
        ".doc",
        ".docx",
        ".xls",
        ".xlsx",
        ".ppt",
        ".pptx",
        ".pyc",
        ".pyo",
        ".class",
        ".o",
        ".a",
        # Data files
        ".json",
    }

    def _should_summarize(self, filepath: str) -> bool:
        """Check if a file should be summarized (not binary, not forge metadata, not excluded)"""
        # Skip forge metadata
        if filepath.startswith(".forge/"):
            return False

        # Skip binary files by extension
        suffix = Path(filepath).suffix.lower()
        if suffix in self._SKIP_EXTENSIONS:
            return False

        # Check user-defined exclusion patterns
        return not self._matches_exclusion_pattern(filepath)

    def _load_exclusion_patterns(self) -> list[str]:
        """Load exclusion patterns from repo config."""
        from forge.ui.summary_exclusions_dialog import load_summary_exclusions

        return load_summary_exclusions(self.vfs)

    def _matches_exclusion_pattern(self, filepath: str) -> bool:
        """Check if a filepath matches any exclusion pattern (gitignore-style)."""
        from forge.ui.summary_exclusions_dialog import matches_pattern

        patterns = self._load_exclusion_patterns()
        return any(matches_pattern(filepath, pattern) for pattern in patterns)

    def build_context(self) -> dict[str, Any]:
        """Build context for LLM with summaries and active files (legacy method)"""
        context = {"summaries": self.repo_summaries, "active_files": {}}

        # Add full content for active files
        for filepath in self.active_files:
            try:
                content = self._repo.get_file_content(filepath, self.branch_name)
                context["active_files"][filepath] = content
            except (FileNotFoundError, KeyError):
                # File may have been deleted
                pass

        return context

    def sync_prompt_manager(self) -> None:
        """
        Sync the prompt manager with current state.

        Call this before building messages to ensure prompt manager
        has current file contents. Summaries are set once at session start,
        not here.

        NOTE: This only adds/removes files. File UPDATES are handled by
        file_was_modified() which is called when tools actually change files.
        This ensures only modified files move to end of prompt (cache optimization).
        """
        current_prompt_files = set(self.prompt_manager.get_active_files())

        # Add files that are newly in context (not already in prompt manager)
        for filepath in self.active_files:
            if filepath not in current_prompt_files:
                try:
                    content = self.tool_manager.vfs.read_file(filepath)
                    self.prompt_manager.append_file_content(filepath, content)
                except (FileNotFoundError, KeyError):
                    pass  # File doesn't exist, skip

        # Remove files that are no longer active
        for filepath in current_prompt_files:
            if filepath not in self.active_files:
                self.prompt_manager.remove_file_content(filepath)

    def add_active_file(self, filepath: str) -> None:
        """Add a file to active context"""
        if filepath in self.active_files:
            return  # Already in context

        self.active_files.add(filepath)

        # Also add to prompt manager with current content (from VFS to include pending changes)
        try:
            content = self.tool_manager.vfs.read_file(filepath)
            self.prompt_manager.append_file_content(filepath, content)
        except (FileNotFoundError, KeyError):
            pass  # File doesn't exist yet

        # Emit signals for UI updates
        self.context_changed.emit(self.active_files.copy())
        self._emit_context_stats()

    def remove_active_file(self, filepath: str) -> None:
        """Remove a file from active context"""
        if filepath not in self.active_files:
            return  # Not in context

        self.active_files.discard(filepath)

        # Also remove from prompt manager
        self.prompt_manager.remove_file_content(filepath)

        # Emit signals for UI updates
        self.context_changed.emit(self.active_files.copy())
        self._emit_context_stats()

    def _emit_context_stats(self) -> None:
        """Emit context stats signal for UI updates"""
        stats = self.get_active_files_with_stats()
        self.context_stats_updated.emit(stats)

    def file_was_modified(self, filepath: str, tool_call_id: str | None = None) -> None:
        """
        Notify that a file was modified (by AI tool).

        This moves the file content to the end of the prompt stream
        so that cache can be reused for content before it.

        If the file isn't already in active context, it gets added
        so the AI can see its changes in subsequent tool calls.

        Args:
            filepath: Path to the modified file
            tool_call_id: The tool call ID that modified this file (for context in prompt)
        """
        # If file not in active context, add it so AI sees its own changes
        if filepath not in self.active_files:
            self.active_files.add(filepath)

        try:
            # Read from VFS to get the NEW content including pending changes
            content = self.tool_manager.vfs.read_file(filepath)
            # append_file_content handles deleting old version and adding new at end
            self.prompt_manager.append_file_content(filepath, content, tool_call_id=tool_call_id)
        except (FileNotFoundError, KeyError):
            # File was deleted
            self.prompt_manager.remove_file_content(filepath)

    def append_user_message(self, content: str) -> None:
        """Add a user message to the prompt stream"""
        self.prompt_manager.append_user_message(content)

    def append_assistant_message(self, content: str) -> None:
        """Add an assistant message to the prompt stream"""
        self.prompt_manager.append_assistant_message(content)

    def append_tool_call(self, tool_calls: list[dict[str, Any]], content: str = "") -> None:
        """Add tool calls to the prompt stream, with any accompanying text content"""
        self.prompt_manager.append_tool_call(tool_calls, content)

    def append_tool_result(
        self, tool_call_id: str, result: str, is_ephemeral: bool = False
    ) -> None:
        """Add a tool result to the prompt stream"""
        self.prompt_manager.append_tool_result(tool_call_id, result, is_ephemeral)

    def mark_mid_turn_commit(self, commit_oid: str = "") -> None:
        """Mark that a commit happened mid-turn (affects end-of-turn commit type)"""
        self._had_mid_turn_commit = True
        if commit_oid:
            self.mid_turn_commit.emit(commit_oid)

    def compact_messages(self, from_id: str, to_id: str, summary: str) -> tuple[int, str | None]:
        """
        Compact conversation messages by replacing them with a summary.

        Args:
            from_id: First message_id to compact (inclusive)
            to_id: Last message_id to compact (inclusive)
            summary: Summary text to replace the content with

        Returns:
            Tuple of (number of blocks compacted, error message or None)
        """
        result = self.prompt_manager.compact_messages(from_id, to_id, summary)
        # Refresh mood bar / context stats after compaction changes token counts
        self._emit_context_stats()
        return result

    def compact_think_call(self, tool_call_id: str) -> bool:
        """
        Compact a think tool call by removing the scratchpad from its arguments.

        The think tool generates extended reasoning in the scratchpad, but only
        the conclusion (in the tool result) needs to be kept in context.

        Args:
            tool_call_id: The ID of the think tool call to compact

        Returns:
            True if the tool call was found and compacted, False otherwise
        """
        return self.prompt_manager.compact_think_call(tool_call_id)

    def get_prompt_messages(self) -> list[dict[str, Any]]:
        """Get the current prompt messages for LLM API"""
        return self.prompt_manager.to_messages()

    @property
    def vfs(self) -> "WorkInProgressVFS":
        """Access the VFS through tool_manager - single source of truth for file content"""
        return self.tool_manager.vfs

    @property
    def repo(self) -> ForgeRepository:
        """Access the git repository."""
        return self._repo

    def _estimate_tokens(self, text: str) -> int:
        """Rough token estimate (3 chars per token average, more accurate for code)"""
        return len(text) // 3

    def get_active_files_with_stats(self) -> dict[str, Any]:
        """Get active files with token counts and context stats"""
        files_info = []
        file_tokens = 0

        for filepath in sorted(self.active_files):
            try:
                content = self.vfs.read_file(filepath)
                tokens = self._estimate_tokens(content)
                file_tokens += tokens
                files_info.append(
                    {"filepath": filepath, "tokens": tokens, "size_bytes": len(content)}
                )
            except Exception as e:
                files_info.append({"filepath": filepath, "error": str(e)})

        # Estimate summary tokens
        summary_tokens = sum(
            self._estimate_tokens(summary) for summary in self.repo_summaries.values()
        )

        # Estimate conversation tokens from prompt manager
        conversation_tokens = self._estimate_conversation_tokens()

        # Estimate system tokens from prompt manager
        system_tokens = self.prompt_manager.estimate_system_tokens()

        return {
            "active_files": files_info,
            "file_tokens": file_tokens,
            "summary_tokens": summary_tokens,
            "conversation_tokens": conversation_tokens,
            "system_tokens": system_tokens,
            "total_context_tokens": file_tokens
            + summary_tokens
            + conversation_tokens
            + system_tokens,
            "file_count": len(files_info),
        }

    def _estimate_conversation_tokens(self) -> int:
        """Estimate tokens used by conversation history (excluding file content)"""
        return self.prompt_manager.estimate_conversation_tokens()

    def get_mood_bar_segments(self) -> list[dict[str, Any]]:
        """Get per-block token breakdown for mood bar visualization."""
        return self.prompt_manager.get_mood_bar_segments()

    def commit_ai_turn(
        self,
        messages: list[dict[str, Any]],
        commit_message: str | None = None,
        session_metadata: dict[str, Any] | None = None,
    ) -> str:
        """
        Commit all changes from an AI turn.

        If there are [prepare] commits (e.g., tool approvals), they will be absorbed automatically.

        Commit type logic:
        - If only session state changed (just conversation): PREPARE commit
        - If actual files changed: MAJOR commit

        Args:
            messages: Session messages to save
            commit_message: Optional commit message (will generate if not provided)
            session_metadata: Optional metadata from SessionRunner (parent/child/state info)

        Returns:
            Commit OID as string
        """
        # Build session state with messages
        session_state = self.get_session_data(messages, session_metadata)

        # Add session state to VFS (single file per branch)
        self.tool_manager.vfs.write_file(SESSION_FILE, json.dumps(session_state, indent=2))

        # Get all changes including session file
        all_changes = self.tool_manager.get_pending_changes()
        deleted_files = self.tool_manager.vfs.get_deleted_files()

        # Determine commit type:
        # - MAJOR if real file changes
        # - FOLLOW_UP if only session changed AND we had a mid-turn commit (suffix to that commit)
        # - PREPARE if only session changed with no mid-turn commit (prefix to next commit)
        has_real_changes = len(all_changes) > 1 or SESSION_FILE not in all_changes or deleted_files
        only_session_changed = not has_real_changes

        if only_session_changed and self._had_mid_turn_commit:
            commit_type = CommitType.FOLLOW_UP
        elif only_session_changed:
            commit_type = CommitType.PREPARE
        else:
            commit_type = CommitType.MAJOR

        # Generate commit message if not provided
        if not commit_message:
            if only_session_changed:
                commit_message = "conversation turn"
            else:
                commit_message = self.generate_commit_message(all_changes)

        # Commit via VFS - handles workdir sync automatically
        commit_oid = self.tool_manager.vfs.commit(commit_message, commit_type=commit_type)

        # Reset mid-turn commit flag for next turn
        self._had_mid_turn_commit = False

        # Refresh VFS to point to new commit (so next turn sees committed state)
        self.tool_manager.vfs = self._create_fresh_vfs()

        return commit_oid

    def _create_fresh_vfs(self) -> "WorkInProgressVFS":
        """Create a fresh VFS pointing to current branch HEAD"""
        from forge.vfs.work_in_progress import WorkInProgressVFS

        return WorkInProgressVFS(self._repo, self.branch_name)

    def generate_commit_message(self, changes: dict[str, str]) -> str:
        """Generate commit message using cheap LLM"""
        model = self.settings.get_summarization_model()
        api_key = self.settings.get_api_key()

        client = LLMClient(api_key, model)

        # Build prompt - filter out session file for description purposes
        # (it always changes but isn't interesting to mention)
        interesting_files = [path for path in changes if path != SESSION_FILE]
        file_list = "\n".join(f"- {path}" for path in interesting_files)

        # Get the last user message for context about what was requested
        last_user_message = self.prompt_manager.get_last_user_message()
        user_context = ""
        if last_user_message:
            user_context = f"\nUser's request:\n{last_user_message}\n"

        prompt = f"""Generate a concise git commit message for these changes.
{user_context}
Files changed:
{file_list}

Respond with ONLY the commit message, no explanation. Use conventional commit format (e.g., "feat:", "fix:", "refactor:").
Keep it under 72 characters."""

        messages = [{"role": "user", "content": prompt}]
        response = client.chat(messages)

        content = response["choices"][0]["message"]["content"]
        message = str(content).strip()
        # Remove quotes if present
        message = message.strip("\"'")

        return message

    def _get_path_depth(self, filepath: str) -> int:
        """Get the depth of a file path (number of directory components)"""
        return filepath.count("/")

    def start_summary_generation(self, force_refresh: bool = False) -> None:
        """
        Start generating repository summaries in a background thread.

        Emits signals:
        - summary_progress(current, total, filepath) during generation
        - summary_finished(count) on completion
        - summary_error(message) on error

        The summaries_ready signal is also emitted with the full dict on completion.
        """
        from forge.ui.chat_workers import SummaryWorker

        if self._summary_thread is not None:
            return  # Already running

        self._summary_thread = QThread()
        self._summary_worker = SummaryWorker(self, force_refresh)
        self._summary_worker.moveToThread(self._summary_thread)

        # Connect worker signals to our signals
        self._summary_worker.progress.connect(self.summary_progress.emit)
        self._summary_worker.finished.connect(self._on_summary_finished)
        self._summary_worker.error.connect(self._on_summary_error)
        self._summary_thread.started.connect(self._summary_worker.run)

        self._summary_thread.start()

    def _on_summary_finished(self, count: int) -> None:
        """Handle summary generation completion."""
        # Clean up thread
        if self._summary_thread:
            self._summary_thread.quit()
            self._summary_thread.wait()
            self._summary_thread = None
            self._summary_worker = None

        self._summaries_ready = True

        # Emit signals
        self.summary_finished.emit(count)
        self.summaries_ready.emit(self.repo_summaries)

        # Auto-add instruction files to context
        for instructions_file in ["CLAUDE.md", "AGENTS.md"]:
            if self.vfs.file_exists(instructions_file):
                self.add_active_file(instructions_file)

        # Emit context stats now that summaries are ready
        self._emit_context_stats()

    def _on_summary_error(self, error_msg: str) -> None:
        """Handle summary generation error."""
        if self._summary_thread:
            self._summary_thread.quit()
            self._summary_thread.wait()
            self._summary_thread = None
            self._summary_worker = None

        self.summary_error.emit(error_msg)

    def generate_repo_summaries(
        self,
        force_refresh: bool = False,
        progress_callback: "Callable[[int, int, str], None] | None" = None,
    ) -> None:
        """
        Generate summaries for files in repository with token budget (breadth-first).

        Files are processed in breadth-first order (by path depth). Summaries are
        generated until the token budget is reached. Files beyond the budget are
        listed without summaries, with a note to use scout for investigation.

        Args:
            force_refresh: If True, regenerate all summaries even if cached
            progress_callback: Optional callback(current, total, filepath) for progress updates
        """
        model = self.settings.get_summarization_model()
        api_key = self.settings.get_api_key()
        client = LLMClient(api_key, model)
        token_budget = self.settings.get_summary_token_budget()

        # List files through VFS (includes any pending new files)
        files = self.vfs.list_files()
        # Filter out forge metadata and binary files
        files = [f for f in files if self._should_summarize(filepath=f)]

        # Sort files breadth-first (by path depth, then alphabetically within each level)
        files.sort(key=lambda f: (self._get_path_depth(f), f))

        # Collect file sizes for all files
        file_sizes: dict[str, int] = {}
        for filepath in files:
            try:
                content = self.vfs.read_file(filepath)
                file_sizes[filepath] = len(content)
            except (FileNotFoundError, KeyError, UnicodeDecodeError):
                file_sizes[filepath] = 0

        # First pass: gather cache info and determine which files need generation
        # Also track running token count to find the cutoff point
        files_with_cache_info: list[tuple[str, str, str | None]] = []
        files_needing_generation: list[tuple[str, str]] = []

        for filepath in files:
            # Get blob OID (content hash) for cache key
            try:
                blob_oid = self._repo.get_file_blob_oid(filepath, self.branch_name)
            except KeyError:
                # File is new (pending), hash the content
                try:
                    content = self.vfs.read_file(filepath)
                except UnicodeDecodeError:
                    # Binary file not in extension list, skip it
                    print(f"   ⚠ {filepath} (binary, skipped)")
                    continue
                blob_oid = hashlib.sha256(content.encode()).hexdigest()

            # Check cache (unless force refresh)
            cached_summary = None
            if not force_refresh:
                cached_summary = self._get_cached_summary(filepath, blob_oid)

            files_with_cache_info.append((filepath, blob_oid, cached_summary))
            if cached_summary is None:
                files_needing_generation.append((filepath, blob_oid))

        # Calculate tokens for cached summaries first, in breadth-first order
        # This determines how many files we can include within budget
        current_tokens = 0
        files_within_budget: list[tuple[str, str, str | None]] = []
        files_beyond_budget: list[str] = []
        budget_cutoff_reached = False

        for filepath, blob_oid, cached_summary in files_with_cache_info:
            if budget_cutoff_reached:
                files_beyond_budget.append(filepath)
                continue

            if cached_summary is not None:
                # Estimate tokens for this summary (filepath header + summary)
                summary_tokens = self._estimate_tokens(f"## {filepath}\n{cached_summary}\n")
                if current_tokens + summary_tokens > token_budget:
                    budget_cutoff_reached = True
                    files_beyond_budget.append(filepath)
                    continue
                current_tokens += summary_tokens
                files_within_budget.append((filepath, blob_oid, cached_summary))
            else:
                # We'll need to generate this one - estimate ~100 tokens as placeholder
                # (actual token count will be checked after generation)
                estimated_tokens = 100
                if current_tokens + estimated_tokens > token_budget:
                    budget_cutoff_reached = True
                    files_beyond_budget.append(filepath)
                    continue
                files_within_budget.append((filepath, blob_oid, None))

        # Filter files_needing_generation to only those within budget
        files_within_budget_set = {f for f, _, _ in files_within_budget}
        files_needing_generation = [
            (f, oid) for f, oid in files_needing_generation if f in files_within_budget_set
        ]

        total_to_generate = len(files_needing_generation)
        total_cached = sum(1 for _, _, cached in files_within_budget if cached is not None)
        parallel_count = self.settings.get_parallel_summarization()
        print(
            f"📚 Summaries: {total_to_generate} to generate, {total_cached} cached, "
            f"{len(files_beyond_budget)} beyond budget (parallel={parallel_count})"
        )

        # Load cached summaries first (only for files within budget)
        for filepath, _blob_oid, cached_summary in files_within_budget:
            if cached_summary is not None:
                self.repo_summaries[filepath] = cached_summary
                print(f"   ✓ {filepath} (cached)")

        # Define worker function for parallel generation
        def generate_one(filepath: str, blob_oid: str) -> tuple[str, str, str] | None:
            """Generate summary for one file. Returns (filepath, blob_oid, summary) or None if binary."""
            try:
                content = self.vfs.read_file(filepath)
            except UnicodeDecodeError:
                # Binary file not in extension list, skip it
                print(f"   ⚠ {filepath} (binary, skipped)")
                return None

            # Truncate very large files for summary generation
            max_chars = 10000
            if len(content) > max_chars:
                content = content[:max_chars] + "\n... (truncated)"

            prompt = self._build_summary_prompt(filepath, content)
            messages = [{"role": "user", "content": prompt}]
            response = client.chat(messages)

            summary_content = response["choices"][0]["message"]["content"]
            summary = str(summary_content).strip()

            # Extract content from <summary> tags if present
            match = re.search(r"<summary>(.*?)</summary>", summary, re.DOTALL)
            if match:
                summary = match.group(1).strip()

            return (filepath, blob_oid, summary)

        # Generate summaries in parallel (only for files within budget)
        if files_needing_generation:
            generated_count = 0
            with ThreadPoolExecutor(max_workers=parallel_count) as executor:
                # Submit all tasks
                future_to_file = {
                    executor.submit(generate_one, filepath, blob_oid): filepath
                    for filepath, blob_oid in files_needing_generation
                }

                # Process results as they complete
                for future in as_completed(future_to_file):
                    filepath = future_to_file[future]
                    generated_count += 1

                    # Report progress
                    if progress_callback:
                        progress_callback(generated_count, total_to_generate, filepath)

                    result = future.result()
                    if result is None:
                        # Binary file, already logged in generate_one
                        continue

                    filepath, blob_oid, summary = result
                    print(f"   📝 {filepath} ({generated_count}/{total_to_generate})")

                    # Cache the summary (even if beyond budget, for future use)
                    self._cache_summary(filepath, blob_oid, summary)
                    self.repo_summaries[filepath] = summary

        # Final progress update (signal completion)
        if progress_callback and total_to_generate > 0:
            progress_callback(total_to_generate, total_to_generate, "")

        # Pass summaries and file info to prompt manager (including beyond-budget files)
        self.prompt_manager.set_summaries(self.repo_summaries, file_sizes, files_beyond_budget)

    def get_session_data(
        self,
        messages: list[dict[str, Any]] | None = None,
        session_metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Get session data for persistence.

        Args:
            messages: Conversation messages
            session_metadata: Optional metadata from SessionRunner (parent/child/state info)
        """
        data: dict[str, Any] = {
            "active_files": list(self.active_files),
            "request_log_entries": [entry.to_dict() for entry in REQUEST_LOG.get_entries()],
        }
        if messages is not None:
            data["messages"] = messages

        # Add session spawn/wait metadata if provided
        if session_metadata:
            data["parent_session"] = session_metadata.get("parent_session")
            data["child_sessions"] = session_metadata.get("child_sessions", [])
            data["state"] = session_metadata.get("state", "idle")
            data["yield_message"] = session_metadata.get("yield_message")

        return data

    def restore_request_log(self, session_data: dict[str, Any]) -> None:
        """Restore request log entries from session data"""
        # Try new format first (full entry dicts with actual_cost)
        log_entries = session_data.get("request_log_entries", [])
        if log_entries:
            REQUEST_LOG.clear()
            for entry_dict in log_entries:
                # Check if files still exist before restoring
                request_file = entry_dict.get("request_file", "")
                if Path(request_file).exists():
                    entry = RequestLogEntry.from_dict(entry_dict)
                    REQUEST_LOG.entries.append(entry)
            return

        # Fall back to old format (just file paths, no actual_cost)
        file_pairs = session_data.get("request_log_files", [])
        if file_pairs:
            REQUEST_LOG.clear()
            for request_file, response_file in file_pairs:
                old_entry = RequestLogEntry.from_files(request_file, response_file)
                if old_entry is not None:
                    REQUEST_LOG.entries.append(old_entry)

    def generate_summary_for_file(self, filepath: str) -> str | None:
        """
        Generate a summary for a single file (used for newly created files).

        Returns the summary text, or None if the file shouldn't be summarized.
        """
        if not self._should_summarize(filepath):
            return None

        # Read content from VFS (includes pending changes)
        try:
            content = self.vfs.read_file(filepath)
        except (FileNotFoundError, KeyError):
            return None

        # Generate blob OID from content hash (file is new/pending)
        blob_oid = hashlib.sha256(content.encode()).hexdigest()

        # Check cache first
        cached_summary = self._get_cached_summary(filepath, blob_oid)
        if cached_summary:
            self.repo_summaries[filepath] = cached_summary
            return cached_summary

        model = self.settings.get_summarization_model()
        api_key = self.settings.get_api_key()
        client = LLMClient(api_key, model)

        # Truncate very large files for summary generation
        max_chars = 10000
        if len(content) > max_chars:
            content = content[:max_chars] + "\n... (truncated)"

        prompt = self._build_summary_prompt(filepath, content)
        messages = [{"role": "user", "content": prompt}]
        response = client.chat(messages)

        summary_content = response["choices"][0]["message"]["content"]
        summary = str(summary_content).strip()

        # Extract content from <summary> tags if present
        match = re.search(r"<summary>(.*?)</summary>", summary, re.DOTALL)
        if match:
            summary = match.group(1).strip()

        # Cache the summary
        self._cache_summary(filepath, blob_oid, summary)
        self.repo_summaries[filepath] = summary

        # Update prompt manager's summaries
        self.prompt_manager.set_summaries(self.repo_summaries)

        return summary
