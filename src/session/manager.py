"""
Session manager for coordinating AI turns and git commits
"""

import json
from typing import Any

from ..git_backend.repository import ForgeRepository
from ..llm.client import LLMClient
from ..tools.manager import ToolManager


class SessionManager:
    """Manages AI session lifecycle and git integration"""

    def __init__(
        self, repo: ForgeRepository, session_id: str, branch_name: str, settings: Any
    ) -> None:
        self.repo = repo
        self.session_id = session_id
        self.branch_name = branch_name
        self.settings = settings

        # Tool manager for this session
        self.tool_manager = ToolManager(repo, branch_name)

        # Active files in context
        self.active_files: set[str] = set()

        # Repository summaries cache
        self.repo_summaries: dict[str, str] = {}

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
        # Get pending changes from tool manager
        changes = self.tool_manager.get_pending_changes()

        # Build session state with messages
        session_state = self.get_session_data(messages)

        session_file_path = f".forge/sessions/{self.session_id}.json"
        changes[session_file_path] = json.dumps(session_state, indent=2)

        # Create tree with all changes
        tree_oid = self.repo.create_tree_from_changes(self.branch_name, changes)

        # Generate commit message if not provided
        if not commit_message:
            commit_message = self.generate_commit_message(changes)

        # Create commit
        commit_oid = self.repo.commit_tree(tree_oid, commit_message, self.branch_name)

        # Clear pending changes
        self.tool_manager.clear_pending_changes()

        return str(commit_oid)

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
        """Generate summaries for all files in repository"""
        # TODO: Implement with cheap LLM
        # For now, just list files
        files = self.repo.get_all_files(self.branch_name)
        for filepath in files:
            if filepath.startswith(".forge/"):
                continue  # Skip forge metadata
            self.repo_summaries[filepath] = f"File: {filepath}"

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
