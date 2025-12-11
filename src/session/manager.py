"""
Session manager for coordinating AI turns and git commits
"""

import hashlib
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..config.settings import Settings

from ..git_backend.repository import ForgeRepository
from ..llm.client import LLMClient
from ..tools.manager import ToolManager


class SessionManager:
    """Manages AI session lifecycle and git integration"""

    def __init__(
        self, repo: ForgeRepository, session_id: str, branch_name: str, settings: "Settings"
    ) -> None:
        self.repo = repo
        self.session_id = session_id
        self.branch_name = branch_name
        self.settings = settings

        # Tool manager for this session
        self.tool_manager = ToolManager(repo, branch_name)

        # Active files in context
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

    def _get_cache_key(self, filepath: str, commit_oid: str) -> str:
        """Generate cache key from filepath and commit OID"""
        # Use hash to keep filename reasonable length
        key_str = f"{commit_oid}:{filepath}"
        return hashlib.sha256(key_str.encode()).hexdigest()

    def _get_cached_summary(self, filepath: str, commit_oid: str) -> str | None:
        """Get cached summary for a file at a specific commit"""
        cache_key = self._get_cache_key(filepath, commit_oid)
        cache_file = self.cache_dir / cache_key

        if cache_file.exists():
            return cache_file.read_text()
        return None

    def _cache_summary(self, filepath: str, commit_oid: str, summary: str) -> None:
        """Cache a summary for a file at a specific commit"""
        cache_key = self._get_cache_key(filepath, commit_oid)
        cache_file = self.cache_dir / cache_key
        cache_file.write_text(summary)

    def build_context(self) -> dict[str, Any]:
        """Build context for LLM with summaries and active files"""
        context = {"summaries": self.repo_summaries, "active_files": {}}

        # Add full content for active files
        for filepath in self.active_files:
            content = self.repo.get_file_content(filepath, self.branch_name)
            context["active_files"][filepath] = content

        return context

    def add_active_file(self, filepath: str) -> None:
        """Add a file to active context"""
        self.active_files.add(filepath)

    def remove_active_file(self, filepath: str) -> None:
        """Remove a file from active context"""
        self.active_files.discard(filepath)

    def commit_ai_turn(
        self, messages: list[dict[str, Any]], commit_message: str | None = None
    ) -> str:
        """
        Commit all changes from an AI turn

        Args:
            messages: Session messages to save
            commit_message: Optional commit message (will generate if not provided)

        Returns:
            Commit OID as string
        """
        # Build session state with messages
        session_state = self.get_session_data(messages)

        # Add session state to VFS
        session_file_path = f".forge/sessions/{self.session_id}.json"
        self.tool_manager.vfs.write_file(session_file_path, json.dumps(session_state, indent=2))

        # Generate commit message if not provided
        if not commit_message:
            # Get all changes including session file
            all_changes = self.tool_manager.get_pending_changes()
            commit_message = self.generate_commit_message(all_changes)

        # Commit via VFS
        commit_oid = self.tool_manager.commit_changes(commit_message)

        return commit_oid

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

    def generate_repo_summaries(self) -> None:
        """Generate summaries for all files in repository (with caching)"""
        # Get current commit OID for cache key
        commit = self.repo.get_branch_head(self.branch_name)
        commit_oid = str(commit.id)

        # Use cheap model for summaries
        model = self.settings.get("git.commit_message_model", "anthropic/claude-3-haiku")
        api_key = self.settings.get_api_key()
        client = LLMClient(api_key, model)

        files = self.repo.get_all_files(self.branch_name)
        for filepath in files:
            if filepath.startswith(".forge/"):
                continue  # Skip forge metadata

            # Check cache first
            cached_summary = self._get_cached_summary(filepath, commit_oid)
            if cached_summary:
                self.repo_summaries[filepath] = cached_summary
                continue

            # Generate summary with cheap LLM
            content = self.repo.get_file_content(filepath, self.branch_name)

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
            self._cache_summary(filepath, commit_oid, summary)
            self.repo_summaries[filepath] = summary

    def get_session_data(self, messages: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        """Get session data for persistence"""
        data: dict[str, Any] = {
            "session_id": self.session_id,
            "branch_name": self.branch_name,
            "active_files": list(self.active_files),
            "repo_summaries": self.repo_summaries,
        }
        if messages is not None:
            data["messages"] = messages
        return data
