"""
Tool manager for discovering and executing tools
"""

import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pygit2

from ..git_backend.commit_types import CommitType

if TYPE_CHECKING:
    from ..git_backend.repository import ForgeRepository


class ToolManager:
    """Manages tools available to the LLM"""

    # Built-in tools that are always approved
    BUILTIN_TOOLS = {
        "read_file",
        "write_file",
        "delete_file",
        "search_replace",
        "update_context",
        "list_active_files",
    }

    def __init__(
        self,
        repo: "ForgeRepository",
        branch_name: str,
        tools_dir: str = "./tools",
    ) -> None:
        # User tools directory (repo-specific)
        self.tools_dir = Path(tools_dir)
        self.tools_dir.mkdir(exist_ok=True)

        # Built-in tools directory (part of Forge)
        self.builtin_tools_dir = Path(__file__).parent / "builtin"

        self.repo = repo
        self.branch_name = branch_name

        # Create VFS for this session
        from ..vfs.work_in_progress import WorkInProgressVFS

        self.vfs: WorkInProgressVFS = WorkInProgressVFS(repo, branch_name)

        # Schema cache
        self._schema_cache: dict[str, dict[str, Any]] = {}

        # Loaded tool modules cache
        self._tool_modules: dict[str, Any] = {}

        # Approved tools tracking
        self.approved_tools_path = ".forge/approved_tools.json"
        self._approved_tools: dict[str, str] = {}  # tool_name -> file_hash
        self._pending_approvals: dict[str, str] = {}  # Changes to amend onto last commit
        self._load_approved_tools()

    def _load_approved_tools(self) -> None:
        """Load approved tools from git commit"""
        try:
            content = self.repo.get_file_content(self.approved_tools_path, self.branch_name)
            self._approved_tools = json.loads(content)
        except (FileNotFoundError, KeyError):
            # File doesn't exist yet, start with empty dict
            self._approved_tools = {}

    def _get_file_hash(self, filepath: Path) -> str:
        """Get SHA256 hash of a file"""
        with open(filepath, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()

    def is_tool_approved(self, tool_name: str) -> bool:
        """Check if a tool is approved and hasn't been modified"""
        # Built-in tools are always approved
        if tool_name in self.BUILTIN_TOOLS:
            return True

        tool_path = self.tools_dir / f"{tool_name}.py"
        if not tool_path.exists():
            return False

        current_hash = self._get_file_hash(tool_path)

        # Check if tool is in approved list with matching hash
        return self._approved_tools.get(tool_name) == current_hash

    def approve_tool(self, tool_name: str) -> None:
        """Approve a tool (records its current hash in pending approvals)"""
        tool_path = self.tools_dir / f"{tool_name}.py"
        if not tool_path.exists():
            raise FileNotFoundError(f"Tool not found: {tool_name}")

        current_hash = self._get_file_hash(tool_path)
        self._approved_tools[tool_name] = current_hash

        # Track in pending approvals (will be amended onto last commit)
        self._pending_approvals[tool_name] = current_hash

    def reject_tool(self, tool_name: str) -> None:
        """Reject a tool (removes from approved list if present)"""
        if tool_name in self._approved_tools:
            del self._approved_tools[tool_name]

        # Track rejection in pending approvals
        if tool_name in self._pending_approvals:
            del self._pending_approvals[tool_name]

    def commit_pending_approvals(self) -> pygit2.Oid | None:
        """
        Commit pending tool approvals as a [follow-up] commit.

        This amends the previous commit (which added/modified the tool).

        Returns:
            New commit OID if there were pending approvals, None otherwise
        """
        if not self._pending_approvals:
            return None

        # Generate approved_tools.json content
        content = json.dumps(self._approved_tools, indent=2)
        tool_names = ", ".join(self._pending_approvals.keys())

        # Create tree with approval changes
        tree_oid = self.repo.create_tree_from_changes(
            self.branch_name, {self.approved_tools_path: content}
        )

        # Create [follow-up] commit - amends the previous commit
        message = f"approve tools: {tool_names}"
        new_commit_oid = self.repo.commit_tree(
            tree_oid, message, self.branch_name, commit_type=CommitType.FOLLOW_UP
        )

        # Clear pending approvals
        self._pending_approvals.clear()

        return new_commit_oid

    def get_unapproved_tools(self) -> list[tuple[str, str, bool, str | None]]:
        """
        Get list of unapproved tools (excludes built-in tools).

        Returns:
            List of (tool_name, current_code, is_new, old_code) tuples
        """
        unapproved: list[tuple[str, str, bool, str | None]] = []

        for tool_file in self.tools_dir.iterdir():
            if tool_file.suffix == ".py" and tool_file.name != "__init__.py":
                tool_name = tool_file.stem

                # Skip built-in tools
                if tool_name in self.BUILTIN_TOOLS:
                    continue

                current_code = tool_file.read_text()

                if not self.is_tool_approved(tool_name):
                    # Check if it's new or modified
                    is_new = tool_name not in self._approved_tools
                    old_code = None

                    # For modified tools, we don't have the old code easily accessible
                    # Could read from git history, but for now just mark as modified
                    unapproved.append((tool_name, current_code, is_new, old_code))

        return unapproved

    def discover_tools(self, force_refresh: bool = False) -> list[dict[str, Any]]:
        """Discover all APPROVED tools (built-in + user) and get their schemas"""
        if not force_refresh and self._schema_cache:
            return list(self._schema_cache.values())

        tools: list[dict[str, Any]] = []
        self._schema_cache = {}
        self._tool_modules = {}

        # Load built-in tools first (always approved)
        for tool_file in self.builtin_tools_dir.iterdir():
            if tool_file.suffix == ".py" and tool_file.name != "__init__.py":
                tool_name = tool_file.stem
                tool_module = self._load_tool_module(tool_file, is_builtin=True)
                if tool_module and hasattr(tool_module, "get_schema"):
                    schema = tool_module.get_schema()
                    tools.append(schema)
                    self._schema_cache[tool_name] = schema
                    self._tool_modules[tool_name] = tool_module

        # Load user tools (only if approved)
        for tool_file in self.tools_dir.iterdir():
            if tool_file.suffix == ".py" and tool_file.name != "__init__.py":
                tool_name = tool_file.stem

                # Only include approved tools
                if not self.is_tool_approved(tool_name):
                    continue

                tool_module = self._load_tool_module(tool_file, is_builtin=False)
                if tool_module and hasattr(tool_module, "get_schema"):
                    schema = tool_module.get_schema()
                    tools.append(schema)
                    self._schema_cache[tool_name] = schema
                    self._tool_modules[tool_name] = tool_module

        return tools

    def _load_tool_module(self, tool_path: Path, is_builtin: bool = False) -> Any:
        """Load a tool as a Python module"""
        if is_builtin:
            module_name = f"src.tools.builtin.{tool_path.stem}"
        else:
            module_name = f"tools.{tool_path.stem}"

        spec = importlib.util.spec_from_file_location(module_name, tool_path)
        assert spec is not None and spec.loader is not None, f"Failed to load spec for {tool_path}"

        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)

        return module

    def execute_tool(
        self, tool_name: str, args: dict[str, Any], session_manager: Any = None
    ) -> dict[str, Any]:
        """Execute a tool with VFS (only if approved)"""
        # Check if tool is approved
        if not self.is_tool_approved(tool_name):
            return {"error": f"Tool {tool_name} is not approved. Cannot execute."}

        if tool_name not in self._tool_modules:
            # Try to load it - check built-in first, then user tools
            builtin_path = self.builtin_tools_dir / f"{tool_name}.py"
            user_path = self.tools_dir / f"{tool_name}.py"

            if builtin_path.exists():
                tool_module = self._load_tool_module(builtin_path, is_builtin=True)
            elif user_path.exists():
                tool_module = self._load_tool_module(user_path, is_builtin=False)
            else:
                return {"error": f"Tool {tool_name} not found"}

            if not tool_module:
                return {"error": f"Failed to load tool {tool_name}"}

            self._tool_modules[tool_name] = tool_module

        tool_module = self._tool_modules[tool_name]

        if not hasattr(tool_module, "execute"):
            return {"error": f"Tool {tool_name} has no execute function"}

        # Execute tool with VFS
        result: dict[str, Any] = tool_module.execute(self.vfs, args)

        # Handle context management actions
        if session_manager and "action" in result:
            action = result["action"]
            if action == "update_context":
                # Handle add/remove in one operation
                add_files = result.get("add", [])
                remove_files = result.get("remove", [])
                for filepath in add_files:
                    session_manager.add_active_file(filepath)
                for filepath in remove_files:
                    session_manager.remove_active_file(filepath)
            elif action == "list_active_files":
                # Get active files with token counts
                result["active_files"] = session_manager.get_active_files_with_stats()

        return result

    def get_pending_changes(self) -> dict[str, str]:
        """Get all pending changes from VFS"""
        return self.vfs.get_pending_changes()

    def clear_pending_changes(self) -> None:
        """Clear pending changes in VFS"""
        self.vfs.clear_pending_changes()

    def commit_changes(
        self,
        message: str,
        author_name: str = "Forge AI",
        author_email: str = "ai@forge.dev",
    ) -> str:
        """Commit all pending changes via VFS"""
        return self.vfs.commit(message, author_name, author_email)
