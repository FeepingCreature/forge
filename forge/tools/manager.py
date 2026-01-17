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

from forge.constants import APPROVED_TOOLS_FILE
from forge.git_backend.commit_types import CommitType

if TYPE_CHECKING:
    from forge.git_backend.repository import ForgeRepository


def _discover_builtin_tools() -> set[str]:
    """Discover all built-in tools from the builtin directory"""
    builtin_dir = Path(__file__).parent / "builtin"
    tools = set()
    for tool_file in builtin_dir.iterdir():
        if tool_file.suffix == ".py" and tool_file.name != "__init__.py":
            tools.add(tool_file.stem)
    return tools


class ToolManager:
    """Manages tools available to the LLM"""

    # Built-in tools that are always approved (auto-discovered from builtin/)
    BUILTIN_TOOLS = _discover_builtin_tools()

    def __init__(
        self,
        repo: "ForgeRepository",
        branch_name: str,
        tools_dir: str = "./tools",
    ) -> None:
        # User tools directory path (repo-specific, accessed via VFS)
        self.tools_dir = Path(tools_dir)

        # Built-in tools directory (part of Forge)
        self.builtin_tools_dir = Path(__file__).parent / "builtin"

        self.branch_name = branch_name

        # Keep repo reference private - only used for VFS creation and approval loading
        self._repo = repo

        # Create VFS for this session - this is THE source of truth for file content
        from forge.vfs.work_in_progress import WorkInProgressVFS

        self.vfs: WorkInProgressVFS = WorkInProgressVFS(repo, branch_name)

        # Schema cache
        self._schema_cache: dict[str, dict[str, Any]] = {}

        # Loaded tool modules cache
        self._tool_modules: dict[str, Any] = {}

        # Approved tools tracking
        self.approved_tools_path = APPROVED_TOOLS_FILE
        self._approved_tools: dict[str, str] = {}  # tool_name -> file_hash
        self._pending_approvals: dict[str, str] = {}  # Changes to amend onto last commit
        self._load_approved_tools()

    def _load_approved_tools(self) -> None:
        """Load approved tools from VFS (git commit + pending changes)"""
        try:
            content = self.vfs.read_file(self.approved_tools_path)
            self._approved_tools = json.loads(content)
        except FileNotFoundError:
            # File doesn't exist yet, start with empty dict
            self._approved_tools = {}

    def _get_file_hash(self, filepath: Path) -> str:
        """Get SHA256 hash of a file on disk"""
        with open(filepath, "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()

    def _get_content_hash(self, content: str) -> str:
        """Get SHA256 hash of content string"""
        return hashlib.sha256(content.encode()).hexdigest()

    def _get_tool_content(self, tool_name: str) -> str | None:
        """Get tool content from VFS or filesystem"""
        # Normalize path (remove ./ prefix if present)
        tools_dir_str = str(self.tools_dir).lstrip("./")
        vfs_path = f"{tools_dir_str}/{tool_name}.py"

        # Check VFS first (includes pending changes)
        if self.vfs.file_exists(vfs_path):
            return self.vfs.read_file(vfs_path)

        # No filesystem fallback - VFS is the single source of truth
        return None

    def is_tool_approved(self, tool_name: str) -> bool:
        """Check if a tool is approved and hasn't been modified"""
        # Built-in tools are always approved
        if tool_name in self.BUILTIN_TOOLS:
            return True

        content = self._get_tool_content(tool_name)
        if content is None:
            return False

        current_hash = self._get_content_hash(content)

        # Check if tool is in approved list with matching hash
        return self._approved_tools.get(tool_name) == current_hash

    def approve_tool(self, tool_name: str) -> None:
        """Approve a tool (records its current hash in pending approvals)"""
        content = self._get_tool_content(tool_name)
        if content is None:
            raise FileNotFoundError(f"Tool not found: {tool_name}")

        current_hash = self._get_content_hash(content)
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
        tree_oid = self._repo.create_tree_from_changes(
            self.branch_name, {self.approved_tools_path: content}
        )

        # Create [follow-up] commit - amends the previous commit
        message = f"approve tools: {tool_names}"
        new_commit_oid = self._repo.commit_tree(
            tree_oid, message, self.branch_name, commit_type=CommitType.FOLLOW_UP
        )

        # Clear pending approvals
        self._pending_approvals.clear()

        return new_commit_oid

    def get_unapproved_tools(self) -> list[tuple[str, str, bool, str | None]]:
        """
        Get list of unapproved tools (excludes built-in tools).

        Uses VFS exclusively to find tools - this includes both committed files
        and pending changes from AI edits.

        Returns:
            List of (tool_name, current_code, is_new, old_code) tuples
        """
        unapproved: list[tuple[str, str, bool, str | None]] = []

        # Normalize the tools_dir path (remove ./ prefix if present)
        tools_prefix = str(self.tools_dir).lstrip("./") + "/"

        # Iterate ALL files in VFS (committed + pending), filter to tools directory
        for filepath in self.vfs.list_files():
            if not filepath.startswith(tools_prefix) or not filepath.endswith(".py"):
                continue

            tool_name = filepath[len(tools_prefix) : -3]  # Remove prefix and .py

            # Skip __init__ and built-in tools
            if tool_name == "__init__" or tool_name in self.BUILTIN_TOOLS:
                continue

            # Read content from VFS (includes pending changes)
            current_code = self.vfs.read_file(filepath)

            if not self.is_tool_approved(tool_name):
                # Check if it's new or modified
                is_new = tool_name not in self._approved_tools
                old_code = None
                unapproved.append((tool_name, current_code, is_new, old_code))

        return unapproved

    def discover_tools(self, force_refresh: bool = False) -> list[dict[str, Any]]:
        """Discover all APPROVED tools (built-in + user) and get their schemas.

        Only returns API tools (tools with invocation="api" or no invocation specified).
        Inline tools (invocation="inline") are handled separately via inline command parsing.

        Args:
            force_refresh: If True, ignore cache and reload all tools
        """
        if not force_refresh and self._schema_cache:
            return self._filter_inline_tools(list(self._schema_cache.values()))

        tools: list[dict[str, Any]] = []
        self._schema_cache = {}
        self._tool_modules = {}

        # Load built-in tools first (always approved)
        # Built-in tools are part of the Forge package, so we use filesystem here
        for tool_file in self.builtin_tools_dir.iterdir():
            if tool_file.suffix == ".py" and tool_file.name != "__init__.py":
                tool_name = tool_file.stem
                tool_module = self._load_tool_module(tool_name, is_builtin=True)
                if tool_module and hasattr(tool_module, "get_schema"):
                    schema = tool_module.get_schema()
                    tools.append(schema)
                    self._schema_cache[tool_name] = schema
                    self._tool_modules[tool_name] = tool_module

        # Load user tools from VFS (includes committed + pending changes)
        tools_prefix = str(self.tools_dir).lstrip("./") + "/"
        for filepath in self.vfs.list_files():
            if not filepath.startswith(tools_prefix) or not filepath.endswith(".py"):
                continue

            tool_name = filepath[len(tools_prefix) : -3]  # Remove prefix and .py

            if tool_name == "__init__":
                continue

            # Only include approved tools
            if not self.is_tool_approved(tool_name):
                continue

            tool_module = self._load_tool_module(tool_name, is_builtin=False)
            if tool_module and hasattr(tool_module, "get_schema"):
                schema = tool_module.get_schema()
                tools.append(schema)
                self._schema_cache[tool_name] = schema
                self._tool_modules[tool_name] = tool_module

        return self._filter_inline_tools(tools)

    def _filter_inline_tools(self, tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Filter out inline tools - they use XML syntax, not API calls.

        Tools with invocation="inline" are handled by the inline command parser,
        not the API tool calling mechanism.
        """
        return [t for t in tools if t.get("invocation", "api") != "inline"]

    def _load_tool_module_from_path(self, tool_path: Path, is_builtin: bool = False) -> Any:
        """Load a tool as a Python module from disk"""
        if is_builtin:
            module_name = f"forge.tools.builtin.{tool_path.stem}"
        else:
            module_name = f"tools.{tool_path.stem}"

        spec = importlib.util.spec_from_file_location(module_name, tool_path)
        assert spec is not None and spec.loader is not None, f"Failed to load spec for {tool_path}"

        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)

        return module

    def _load_tool_module_from_source(self, tool_name: str, source: str) -> Any:
        """Load a tool as a Python module from source code string (for VFS-only tools)"""
        module_name = f"tools.{tool_name}"

        # Create a new module
        import types

        module = types.ModuleType(module_name)
        module.__file__ = f"<vfs>/tools/{tool_name}.py"

        # Execute the source in the module's namespace
        exec(compile(source, module.__file__, "exec"), module.__dict__)

        # Register in sys.modules
        sys.modules[module_name] = module

        return module

    def _load_tool_module(self, tool_name: str, is_builtin: bool = False) -> Any:
        """Load a tool module - from disk for built-ins, from VFS for user tools"""
        if is_builtin:
            tool_path = self.builtin_tools_dir / f"{tool_name}.py"
            return self._load_tool_module_from_path(tool_path, is_builtin=True)

        # For user tools, load from VFS (single source of truth)
        content = self._get_tool_content(tool_name)
        if content is not None:
            return self._load_tool_module_from_source(tool_name, content)

        return None

    def execute_tool(
        self, tool_name: str, args: dict[str, Any], session_manager: Any = None
    ) -> dict[str, Any]:
        """Execute a tool with VFS or ToolContext based on API version."""
        from forge.tools.context import ToolContext, get_tool_api_version

        # Check if tool is approved
        if not self.is_tool_approved(tool_name):
            return {"error": f"Tool {tool_name} is not approved. Cannot execute."}

        if tool_name not in self._tool_modules:
            # Determine if it's a built-in tool
            is_builtin = tool_name in self.BUILTIN_TOOLS

            # Load the tool module (handles VFS and disk)
            tool_module = self._load_tool_module(tool_name, is_builtin=is_builtin)

            if not tool_module:
                return {"error": f"Tool {tool_name} not found"}

            self._tool_modules[tool_name] = tool_module

        tool_module = self._tool_modules[tool_name]

        if not hasattr(tool_module, "execute"):
            return {"error": f"Tool {tool_name} has no execute function"}

        # Detect API version and execute appropriately
        api_version = get_tool_api_version(tool_module.execute)

        if api_version == 2:
            # v2: Pass ToolContext with full access
            from forge.session.registry import SESSION_REGISTRY

            ctx = ToolContext(
                vfs=self.vfs,
                repo=self._repo,
                branch_name=self.branch_name,
                session_manager=session_manager,
                registry=SESSION_REGISTRY,
            )
            result: dict[str, Any] = tool_module.execute(ctx, args)
        else:
            # v1: Pass VFS only (backwards compatible)
            result = tool_module.execute(self.vfs, args)

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

        return result

    def get_pending_changes(self) -> dict[str, str]:
        """Get all pending changes from VFS"""
        return self.vfs.get_pending_changes()

    def clear_pending_changes(self) -> None:
        """Clear pending changes in VFS"""
        self.vfs.clear_pending_changes()
