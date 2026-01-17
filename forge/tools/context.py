"""
ToolContext - Rich context object for tool API v2.

Tools can receive either:
- v1: execute(vfs: VFS, args: dict) - basic file access only
- v2: execute(ctx: ToolContext, args: dict) - full context access

The API version is detected by inspecting the first parameter's type annotation.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from forge.git_backend.repository import ForgeRepository
    from forge.session.manager import SessionManager
    from forge.session.registry import SessionRegistry
    from forge.vfs.work_in_progress import WorkInProgressVFS


@dataclass
class ToolContext:
    """
    Rich context for tool execution (API v2).

    Provides access to everything a tool might need:
    - vfs: File system access for current branch
    - repo: Git repository for cross-branch operations
    - branch_name: Current branch name
    - session_manager: Session state (optional, for session tools)
    - registry: Session registry (for spawn/wait tools)
    """

    vfs: "WorkInProgressVFS"
    repo: "ForgeRepository"
    branch_name: str
    session_manager: "SessionManager | None" = None
    registry: "SessionRegistry | None" = None

    # Convenience methods that tools commonly need

    def read_file(self, path: str) -> str:
        """Read a file from the VFS."""
        return self.vfs.read_file(path)

    def write_file(self, path: str, content: str) -> None:
        """Write a file to the VFS."""
        self.vfs.write_file(path, content)

    def file_exists(self, path: str) -> bool:
        """Check if a file exists."""
        return self.vfs.file_exists(path)

    def list_files(self) -> list[str]:
        """List all text files in the VFS."""
        return self.vfs.list_files()

    def get_branch_vfs(self, branch_name: str) -> "WorkInProgressVFS":
        """Get a VFS for another branch (for cross-branch operations)."""
        from forge.vfs.work_in_progress import WorkInProgressVFS

        return WorkInProgressVFS(self.repo, branch_name)


def get_tool_api_version(execute_func: Any) -> int:
    """
    Detect tool API version from execute function's type annotations.

    Returns:
        1 if first param is VFS (or no annotation)
        2 if first param is ToolContext
    """
    import inspect

    sig = inspect.signature(execute_func)
    params = list(sig.parameters.values())

    if not params:
        return 1  # No params, assume v1

    first_param = params[0]
    annotation = first_param.annotation

    if annotation is inspect.Parameter.empty:
        return 1  # No annotation, assume v1

    # Check if annotation is ToolContext (handle string annotations too)
    if annotation is ToolContext:
        return 2

    # Handle string annotation (from __future__ import annotations)
    if isinstance(annotation, str) and "ToolContext" in annotation:
        return 2

    # Check __name__ for forward references
    if hasattr(annotation, "__name__") and annotation.__name__ == "ToolContext":
        return 2

    return 1
