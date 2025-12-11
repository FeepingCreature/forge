"""
Tool manager for discovering and executing tools
"""

import importlib.util
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from ..git_backend.repository import ForgeRepository
    from ..vfs.work_in_progress import WorkInProgressVFS


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

    def discover_tools(self, force_refresh: bool = False) -> list[dict[str, Any]]:
        """Discover all tools and get their schemas"""
        if not force_refresh and self._schema_cache:
            return list(self._schema_cache.values())

        tools: list[dict[str, Any]] = []
        self._schema_cache = {}
        self._tool_modules = {}

        if not self.tools_dir.exists():
            return tools

        for tool_file in self.tools_dir.iterdir():
            if tool_file.suffix == ".py" and tool_file.name != "__init__.py":
                tool_module = self._load_tool_module(tool_file)
                if tool_module and hasattr(tool_module, "get_schema"):
                    schema = tool_module.get_schema()
                    tool_name = tool_file.stem
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
        """Execute a tool with VFS"""
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
        return tool_module.execute(self.vfs, args)

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
