"""
Tool manager for discovering and executing tools
"""

import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..git_backend.repository import ForgeRepository


class ToolManager:
    """Manages tools available to the LLM"""

    def __init__(
        self,
        repo: "ForgeRepository",
        branch_name: str,
        tools_dir: str = "./tools",
    ) -> None:
        self.tools_dir = Path(tools_dir)
        self.tools_dir.mkdir(exist_ok=True)
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
        self.approved_tools_file = Path(".forge/approved_tools.json")
        self._approved_tools: dict[str, str] = {}  # tool_name -> file_hash
        self._load_approved_tools()

    def _load_approved_tools(self) -> None:
        """Load approved tools from .forge/approved_tools.json"""
        if self.approved_tools_file.exists():
            with open(self.approved_tools_file) as f:
                self._approved_tools = json.load(f)
        else:
            self._approved_tools = {}

    def _save_approved_tools(self) -> None:
        """Save approved tools to .forge/approved_tools.json"""
        self.approved_tools_file.parent.mkdir(parents=True, exist_ok=True)
        with open(self.approved_tools_file, "w") as f:
            json.dump(self._approved_tools, f, indent=2)

    def _get_file_hash(self, filepath: Path) -> str:
        """Get SHA256 hash of a file"""
        with open(filepath, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()

    def is_tool_approved(self, tool_name: str) -> bool:
        """Check if a tool is approved and hasn't been modified"""
        tool_path = self.tools_dir / f"{tool_name}.py"
        if not tool_path.exists():
            return False

        current_hash = self._get_file_hash(tool_path)

        # Check if tool is in approved list with matching hash
        return self._approved_tools.get(tool_name) == current_hash

    def approve_tool(self, tool_name: str) -> None:
        """Approve a tool (records its current hash)"""
        tool_path = self.tools_dir / f"{tool_name}.py"
        if not tool_path.exists():
            raise FileNotFoundError(f"Tool not found: {tool_name}")

        current_hash = self._get_file_hash(tool_path)
        self._approved_tools[tool_name] = current_hash
        self._save_approved_tools()

    def reject_tool(self, tool_name: str) -> None:
        """Reject a tool (removes from approved list if present)"""
        if tool_name in self._approved_tools:
            del self._approved_tools[tool_name]
            self._save_approved_tools()

    def get_unapproved_tools(self) -> list[tuple[str, str, bool, str | None]]:
        """
        Get list of unapproved tools.

        Returns:
            List of (tool_name, current_code, is_new, old_code) tuples
        """
        unapproved = []

        for tool_file in self.tools_dir.iterdir():
            if tool_file.suffix == ".py" and tool_file.name != "__init__.py":
                tool_name = tool_file.stem
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
        """Discover all APPROVED tools and get their schemas"""
        if not force_refresh and self._schema_cache:
            return list(self._schema_cache.values())

        tools: list[dict[str, Any]] = []
        self._schema_cache = {}
        self._tool_modules = {}

        for tool_file in self.tools_dir.iterdir():
            if tool_file.suffix == ".py" and tool_file.name != "__init__.py":
                tool_name = tool_file.stem

                # Only include approved tools
                if not self.is_tool_approved(tool_name):
                    continue

                tool_module = self._load_tool_module(tool_file)
                if tool_module and hasattr(tool_module, "get_schema"):
                    schema = tool_module.get_schema()
                    tools.append(schema)
                    self._schema_cache[tool_name] = schema
                    self._tool_modules[tool_name] = tool_module

        return tools

    def _load_tool_module(self, tool_path: Path) -> Any:
        """Load a tool as a Python module"""
        module_name = f"tools.{tool_path.stem}"

        spec = importlib.util.spec_from_file_location(module_name, tool_path)
        if spec is None or spec.loader is None:
            return None

        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)

        return module

    def execute_tool(self, tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
        """Execute a tool with VFS (only if approved)"""
        # Check if tool is approved
        if not self.is_tool_approved(tool_name):
            return {"error": f"Tool {tool_name} is not approved. Cannot execute."}

        if tool_name not in self._tool_modules:
            # Try to load it
            tool_path = self.tools_dir / f"{tool_name}.py"
            if not tool_path.exists():
                return {"error": f"Tool {tool_name} not found"}

            tool_module = self._load_tool_module(tool_path)
            if not tool_module:
                return {"error": f"Failed to load tool {tool_name}"}

            self._tool_modules[tool_name] = tool_module

        tool_module = self._tool_modules[tool_name]

        if not hasattr(tool_module, "execute"):
            return {"error": f"Tool {tool_name} has no execute function"}

        # Execute tool with VFS
        result: dict[str, Any] = tool_module.execute(self.vfs, args)
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
