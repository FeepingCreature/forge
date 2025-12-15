"""
Session manager for coordinating AI turns and git commits
"""

import hashlib
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..config.settings import Settings
    from ..vfs.work_in_progress import WorkInProgressVFS

from ..git_backend.commit_types import CommitType
from ..git_backend.repository import ForgeRepository
from ..llm.client import LLMClient
from ..prompts.manager import PromptManager
from ..prompts.system import SYSTEM_PROMPT
from ..tools.manager import ToolManager


class SessionManager:
    """Manages AI session lifecycle and git integration"""

    # The single session file path (branch-local, diverges naturally)
    SESSION_FILE = ".forge/session.json"

    def __init__(self, repo: ForgeRepository, branch_name: str, settings: "Settings") -> None:
        self.branch_name = branch_name
        self.settings = settings

        # Tool manager owns the VFS - all file access goes through it
        self.tool_manager = ToolManager(repo, branch_name)

        # Keep repo reference only for commit operations (not file reading)
        self._repo = repo

        # Prompt manager for cache-optimized prompt construction
        self.prompt_manager = PromptManager(SYSTEM_PROMPT)

        # Active files in context (tracked separately for persistence)
        self.active_files: set[str] = set()

        # Repository summaries cache (in-memory)
        self.repo_summaries: dict[str, str] = {}

        # XDG cache directory for persistent summary cache
        self.cache_dir = self._get_cache_dir()
        self.cache_dir.mkdir(parents=True, exist_ok=True)

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

    def build_context(self) -> dict[str, Any]:
        """Build context for LLM with summaries and active files (legacy method)"""
        context = {"summaries": self.repo_summaries, "active_files": {}}

        # Add full content for active files
        for filepath in self.active_files:
            try:
                content = self.repo.get_file_content(filepath, self.branch_name)
                context["active_files"][filepath] = content
            except (FileNotFoundError, KeyError):
                # File may have been deleted
                pass

        return context

    def sync_prompt_manager(self) -> None:
        """
        Sync the prompt manager with current state.

        Call this before building messages to ensure prompt manager
        has current summaries and file contents.
        """
        # Update summaries in prompt manager
        self.prompt_manager.set_summaries(self.repo_summaries)

        # Sync active files - add any that are missing, update any that changed
        current_prompt_files = set(self.prompt_manager.get_active_files())

        for filepath in self.active_files:
            try:
                # Read from VFS to get pending changes, not just committed content
                content = self.tool_manager.vfs.read_file(filepath)

                # Check if file is already in prompt manager
                if filepath in current_prompt_files:
                    # File exists - check if content changed by comparing
                    # For now, always update (could optimize with hashing later)
                    pass

                # Add/update file content (this handles deletion of old version)
                note = ""
                if filepath in current_prompt_files:
                    note = "Content updated - summary at start may be outdated"
                self.prompt_manager.append_file_content(filepath, content, note)

            except (FileNotFoundError, KeyError):
                # File was deleted - remove from prompt manager
                self.prompt_manager.remove_file_content(filepath)

        # Remove files that are no longer active
        for filepath in current_prompt_files:
            if filepath not in self.active_files:
                self.prompt_manager.remove_file_content(filepath)

    def add_active_file(self, filepath: str) -> None:
        """Add a file to active context"""
        self.active_files.add(filepath)

        # Also add to prompt manager with current content (from VFS to include pending changes)
        try:
            content = self.tool_manager.vfs.read_file(filepath)
            self.prompt_manager.append_file_content(filepath, content)
        except (FileNotFoundError, KeyError):
            pass  # File doesn't exist yet

    def remove_active_file(self, filepath: str) -> None:
        """Remove a file from active context"""
        self.active_files.discard(filepath)

        # Also remove from prompt manager
        self.prompt_manager.remove_file_content(filepath)

    def file_was_modified(self, filepath: str) -> None:
        """
        Notify that a file was modified (by AI tool).

        This moves the file content to the end of the prompt stream
        so that cache can be reused for content before it.

        If the file isn't already in active context, it gets added
        so the AI can see its changes in subsequent tool calls.
        """
        # If file not in active context, add it so AI sees its own changes
        if filepath not in self.active_files:
            self.active_files.add(filepath)

        try:
            # Read from VFS to get the NEW content including pending changes
            content = self.tool_manager.vfs.read_file(filepath)
            # append_file_content handles deleting old version and adding new at end
            self.prompt_manager.append_file_content(
                filepath, content, note="Content updated - summary at start may be outdated"
            )
        except (FileNotFoundError, KeyError):
            # File was deleted
            self.prompt_manager.remove_file_content(filepath)

    def append_user_message(self, content: str) -> None:
        """Add a user message to the prompt stream"""
        self.prompt_manager.append_user_message(content)

    def append_assistant_message(self, content: str) -> None:
        """Add an assistant message to the prompt stream"""
        self.prompt_manager.append_assistant_message(content)

    def append_tool_call(self, tool_calls: list[dict[str, Any]]) -> None:
        """Add tool calls to the prompt stream"""
        self.prompt_manager.append_tool_call(tool_calls)

    def append_tool_result(self, tool_call_id: str, result: str) -> None:
        """Add a tool result to the prompt stream"""
        self.prompt_manager.append_tool_result(tool_call_id, result)

    def get_prompt_messages(self) -> list[dict[str, Any]]:
        """Get the current prompt messages for LLM API"""
        return self.prompt_manager.to_messages()

    @property
    def vfs(self) -> "WorkInProgressVFS":
        """Access the VFS through tool_manager - single source of truth for file content"""
        return self.tool_manager.vfs

    def _estimate_tokens(self, text: str) -> int:
        """Rough token estimate (4 chars per token average)"""
        return len(text) // 4

    def get_active_files_with_stats(self) -> dict[str, Any]:
        """Get active files with token counts and context stats"""
        files_info = []
        total_tokens = 0

        for filepath in sorted(self.active_files):
            try:
                content = self.vfs.read_file(filepath)
                tokens = self._estimate_tokens(content)
                total_tokens += tokens
                files_info.append(
                    {"filepath": filepath, "tokens": tokens, "size_bytes": len(content)}
                )
            except Exception as e:
                files_info.append({"filepath": filepath, "error": str(e)})

        # Estimate summary tokens
        summary_tokens = sum(
            self._estimate_tokens(summary) for summary in self.repo_summaries.values()
        )

        return {
            "active_files": files_info,
            "total_active_tokens": total_tokens,
            "summary_tokens": summary_tokens,
            "total_context_tokens": total_tokens + summary_tokens,
            "file_count": len(files_info),
        }

    def commit_ai_turn(
        self, messages: list[dict[str, Any]], commit_message: str | None = None
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

        Returns:
            Commit OID as string
        """
        # Build session state with messages
        session_state = self.get_session_data(messages)

        # Add session state to VFS (single file per branch)
        self.tool_manager.vfs.write_file(self.SESSION_FILE, json.dumps(session_state, indent=2))

        # Get all changes including session file
        all_changes = self.tool_manager.get_pending_changes()
        deleted_files = self.tool_manager.vfs.get_deleted_files()

        # Determine commit type: PREPARE if only session file changed, MAJOR if real changes
        has_real_changes = (
            len(all_changes) > 1 or self.SESSION_FILE not in all_changes or deleted_files
        )
        only_session_changed = not has_real_changes
        commit_type = CommitType.PREPARE if only_session_changed else CommitType.MAJOR

        # Generate commit message if not provided
        if not commit_message:
            if only_session_changed:
                commit_message = "conversation turn"
            else:
                commit_message = self.generate_commit_message(all_changes)

        # Build tree from VFS changes (including deletions)
        tree_oid = self._repo.create_tree_from_changes(self.branch_name, all_changes, deleted_files)

        # Create commit - will automatically absorb any PREPARE commits if MAJOR
        commit_oid = self._repo.commit_tree(
            tree_oid, commit_message, self.branch_name, commit_type=commit_type
        )

        # Clear VFS pending changes and refresh to new HEAD
        self.tool_manager.clear_pending_changes()

        # Refresh VFS to point to new commit (so next turn sees committed state)
        self.tool_manager.vfs = self._create_fresh_vfs()

        return str(commit_oid)

    def _create_fresh_vfs(self) -> "WorkInProgressVFS":
        """Create a fresh VFS pointing to current branch HEAD"""
        from ..vfs.work_in_progress import WorkInProgressVFS

        return WorkInProgressVFS(self._repo, self.branch_name)

    def generate_commit_message(self, changes: dict[str, str]) -> str:
        """Generate commit message using cheap LLM"""
        # Get commit message model
        model = self.settings.get("git.commit_message_model", "anthropic/claude-3-haiku")
        api_key = self.settings.get_api_key()

        client = LLMClient(api_key, model)

        # Build prompt
        file_list = "\n".join(f"- {path}" for path in changes)
        prompt = f"""Generate a concise git commit message for these changes:

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

    def generate_repo_summaries(self, force_refresh: bool = False) -> None:
        """
        Generate summaries for all files in repository (with caching)

        Args:
            force_refresh: If True, regenerate all summaries even if cached
        """
        # Use summarization model (typically a cheap/fast model)
        model = self.settings.get("llm.summarization_model", "anthropic/claude-3-haiku")
        api_key = self.settings.get_api_key()
        client = LLMClient(api_key, model)

        # List files through VFS (includes any pending new files)
        files = self.vfs.list_files()
        print(f"📝 Generating summaries for {len(files)} files (cached summaries will be reused)")

        for filepath in files:
            if filepath.startswith(".forge/"):
                continue  # Skip forge metadata

            # Get blob OID (content hash) for cache key
            # For pending files, use a hash of the content itself
            try:
                blob_oid = self._repo.get_file_blob_oid(filepath, self.branch_name)
            except KeyError:
                # File is new (pending), hash the content
                import hashlib

                content = self.vfs.read_file(filepath)
                blob_oid = hashlib.sha256(content.encode()).hexdigest()

            # Check cache first (unless force refresh)
            if not force_refresh:
                cached_summary = self._get_cached_summary(filepath, blob_oid)
                if cached_summary:
                    self.repo_summaries[filepath] = cached_summary
                    print(f"   ✓ {filepath} (cached)")
                    continue

            # Generate summary with cheap LLM
            print(f"   🔄 {filepath} (generating...)")
            content = self.vfs.read_file(filepath)

            # Truncate very large files for summary generation
            max_chars = 10000
            if len(content) > max_chars:
                content = content[:max_chars] + "\n... (truncated)"

            prompt = f"""Generate a micro-README for this file listing its public interfaces.

File: {filepath}

```
{content}
```

Format as a bulleted list:
- ClassName: brief description
- function_name(): brief description
- CONSTANT: brief description

Only list PUBLIC interfaces (classes, functions, constants that would be imported/used).
Skip private items (starting with _).
Keep each line under 80 chars.
Respond with ONLY the bulleted list, no introduction or explanation."""

            messages = [{"role": "user", "content": prompt}]
            response = client.chat(messages)

            summary_content = response["choices"][0]["message"]["content"]
            summary = str(summary_content).strip().strip("\"'")

            # Cache the summary
            self._cache_summary(filepath, blob_oid, summary)
            self.repo_summaries[filepath] = summary

    def get_session_data(self, messages: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        """Get session data for persistence"""
        data: dict[str, Any] = {
            "active_files": list(self.active_files),
        }
        if messages is not None:
            data["messages"] = messages
        return data
