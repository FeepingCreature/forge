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


def _discover_conditional_tools() -> set[str]:
    """Discover built-in tools that require explicit opt-in via repo config.

    A tool is conditional if its module defines CONDITIONAL = True.
    These tools are only loaded when listed in .forge/config.json "enabled_tools".
    """
    builtin_dir = Path(__file__).parent / "builtin"
    conditional = set()
    for tool_file in builtin_dir.iterdir():
        if tool_file.suffix == ".py" and tool_file.name != "__init__.py":
            # Quick scan for CONDITIONAL = True without importing
            try:
                content = tool_file.read_text()
                if "CONDITIONAL = True" in content:
                    conditional.add(tool_file.stem)
            except OSError:
                pass
    return conditional


class ToolManager:
    """Manages tools available to the LLM"""

    # Built-in tools that are always approved (auto-discovered from builtin/)
    BUILTIN_TOOLS = _discover_builtin_tools()

    # Conditional tools require opt-in via .forge/config.json "enabled_tools"
    CONDITIONAL_TOOLS = _discover_conditional_tools()

    def __init__(
        self,
        repo: "ForgeRepository",
        branch_name: str,
        tools_dir: str = "./tools",
        inline_enabled: bool = True,
        require_done_tag: bool = False,
        prefix_tool_args: bool = False,
    ) -> None:
        # User tools directory path (repo-specific, accessed via VFS)
        self.tools_dir = Path(tools_dir)

        # Whether the inline XML text-parsing path is active. When True,
        # inline-capable tools (edit, commit, run_tests, ...) are invoked by
        # parsing the assistant's prose, so they are NOT exposed as API tools
        # (that would double-expose them). When False, the text-parsing path
        # is off, so those same tools MUST be exposed as API tools — otherwise
        # the model loses the ability to edit files, commit, run tests, etc.
        self.inline_enabled = inline_enabled

        # Whether the strict end-of-turn handshake is active. The `done` tool
        # exists solely to declare SideEffect.END_TURN in that mode. When it's
        # off, `done` is a no-op that actively *prevents* a turn from ending
        # (it's a tool call, so the loop re-drives the model to act on its
        # result) - a footgun. So we only expose `done` when require_done_tag
        # is True.
        self.require_done_tag = require_done_tag
        self.prefix_tool_args = prefix_tool_args

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

        # Skills discovered from tools (skill_name -> documentation)
        self._skills: dict[str, str] = {}

        # Approved tools tracking
        self.approved_tools_path = APPROVED_TOOLS_FILE
        self._approved_tools: dict[str, str] = {}  # tool_name -> file_hash
        self._pending_approvals: dict[str, str] = {}  # Changes to amend onto last commit
        self._load_approved_tools()

        # Load per-repo enabled tools config
        self._enabled_tools: set[str] = self._load_enabled_tools()

    def _load_enabled_tools(self) -> set[str]:
        """Load the list of conditionally-enabled tools from .forge/config.json.

        Conditional built-in tools (those with CONDITIONAL = True) are only
        loaded when explicitly listed in the repo's config file under "enabled_tools".
        """
        try:
            content = self.vfs.read_file(".forge/config.json")
            config = json.loads(content)
            enabled = config.get("enabled_tools", [])
            return set(enabled)
        except (FileNotFoundError, json.JSONDecodeError, KeyError):
            return set()

    def _is_tool_enabled(self, tool_name: str) -> bool:
        """Check if a built-in tool should be loaded.

        Unconditional tools are always enabled.
        Conditional tools require listing in .forge/config.json "enabled_tools".
        """
        if tool_name not in self.CONDITIONAL_TOOLS:
            return True  # Not conditional, always enabled
        return tool_name in self._enabled_tools

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

        When inline parsing is enabled, only returns API tools — inline tools
        (invocation="inline") are handled separately via inline command
        parsing. When inline parsing is disabled, inline tools are also
        returned so the model can invoke them as ordinary API tool calls.

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

                # Skip conditional tools that aren't enabled in repo config
                if not self._is_tool_enabled(tool_name):
                    continue

                tool_module = self._load_tool_module(tool_name, is_builtin=True)
                if tool_module and hasattr(tool_module, "get_schema"):
                    schema = tool_module.get_schema()
                    tools.append(schema)
                    self._schema_cache[tool_name] = schema
                    self._tool_modules[tool_name] = tool_module
                    # Discover skill if present
                    if hasattr(tool_module, "get_skill"):
                        skill_info = tool_module.get_skill()
                        if skill_info:
                            skill_name, skill_doc = skill_info
                            self._skills[skill_name] = skill_doc

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
                # Discover skill if present
                if hasattr(tool_module, "get_skill"):
                    skill_info = tool_module.get_skill()
                    if skill_info:
                        skill_name, skill_doc = skill_info
                        self._skills[skill_name] = skill_doc

        tools = self._apply_arg_prefixing(tools)
        return self._filter_inline_tools(tools)

    def _filter_inline_tools(self, tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Decide which tools are exposed as API tools.

        When inline parsing is ENABLED, tools with invocation="inline" are
        driven by the inline command parser (XML in the assistant's prose),
        so we strip them from the API tool list to avoid double-exposing them.

        When inline parsing is DISABLED, the text-parsing path is off, so those
        same tools would be unreachable — every inline tool also has an
        execute() that works as a normal API tool, so we expose them all. But
        their schemas still carry the inline markers (invocation="inline" and
        an inline_syntax pointing at XML like <replace>/<write>). Exposed as an
        API tool, those markers are a lie — the model can only call the tool as
        a function. So we strip the inline markers before handing them over.
        """
        if not self.require_done_tag:
            tools = [t for t in tools if t.get("function", {}).get("name") != "done"]
        if not self.inline_enabled:
            return [self._strip_inline_markers(t) for t in tools]
        return [t for t in tools if t.get("invocation", "api") != "inline"]

    def _apply_arg_prefixing(self, tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Prefix tool arguments with 1_, 2_, etc. to force alphanumeric order."""
        if not self.prefix_tool_args:
            return tools

        for tool in tools:
            func = tool.get("function", {})
            params = func.get("parameters", {})
            if not params or "properties" not in params:
                continue

            properties = params["properties"]
            name_map = {}
            new_properties = {}
            for i, (prop_name, prop_val) in enumerate(properties.items(), 1):
                prefixed_name = f"{i}_{prop_name}"
                name_map[prop_name] = prefixed_name
                new_properties[prefixed_name] = prop_val

            params["properties"] = new_properties

            required = params.get("required", [])
            if required:
                params["required"] = [name_map[name] for name in required if name in name_map]

        return tools

    @staticmethod
    def _strip_inline_markers(schema: dict[str, Any]) -> dict[str, Any]:
        """Return a copy of a tool schema with inline-only markers removed.

        Strips the top-level "invocation" and "inline_syntax" keys so a tool
        that is being exposed as an API function isn't also advertising XML
        inline syntax it can't actually be invoked with in this mode. Schemas
        without those keys are returned unchanged (a shallow copy).
        """
        if "invocation" not in schema and "inline_syntax" not in schema:
            return schema
        stripped = {k: v for k, v in schema.items() if k not in ("invocation", "inline_syntax")}
        return stripped

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
        if self.prefix_tool_args:
            import re

            args = {re.sub(r"^\d+_", "", k): v for k, v in args.items()}

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

        # Tool execution is a trust boundary: the model (especially small/local
        # models) may pass arguments that violate the schema and trigger
        # exceptions deep in the tool. We catch those here and return them as
        # tool-error results so the model can self-correct, instead of letting
        # them tear down the worker thread and the UI with it.
        #
        # This is one of the few sanctioned try/except sites in the codebase
        # (see CLAUDE.md "No fallbacks") - it exists specifically to translate
        # tool-internal failures into model-visible feedback.
        try:
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
        except Exception as e:
            import traceback

            tb = traceback.format_exc()
            # Still print to stderr so the developer sees it in the terminal -
            # we're translating it for the model, not hiding it.
            print(
                f"⚠️  Tool {tool_name!r} raised {type(e).__name__}; "
                f"returning as tool-error result:\n{tb}",
                file=sys.stderr,
            )
            return {
                "success": False,
                "error": (
                    f"Tool {tool_name!r} raised {type(e).__name__}: {e}. "
                    "This usually means the arguments did not match the tool's schema. "
                    "Check the schema and retry with corrected arguments."
                ),
                "traceback": tb,
            }

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

    def get_skills(self) -> dict[str, str]:
        """Get all discovered skills from tools.

        Returns:
            Dict mapping skill_name -> documentation
        """
        return self._skills.copy()

    def get_pending_changes(self) -> dict[str, str]:
        """Get all pending changes from VFS"""
        return self.vfs.get_pending_changes()

    def clear_pending_changes(self) -> None:
        """Clear pending changes in VFS"""
        self.vfs.clear_pending_changes()
