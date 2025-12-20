"""
Run make check (format + typecheck + lint) on the current VFS state.

This tool:
1. Materializes the VFS to a temp directory
2. Runs `make format` then `make typecheck` then `make lint-check`
3. Reads back any files modified by formatting and updates the VFS
4. Returns errors and a list of files that were auto-formatted
"""

import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from forge.vfs.work_in_progress import WorkInProgressVFS


def get_schema() -> dict[str, Any]:
    """Return tool schema for LLM"""
    return {
        "type": "function",
        "function": {
            "name": "check",
            "description": """Run make check (format + typecheck + lint) on the current VFS state.

This materializes your changes to a temp directory, runs the checks, and:
- Auto-applies any formatting changes back to the VFS
- Returns type errors and lint errors for you to fix

Call this before finishing to ensure your code passes all checks.""",
            "parameters": {
                "type": "object",
                "properties": {},
            },
        },
    }


def execute(vfs: "WorkInProgressVFS", args: dict[str, Any]) -> dict[str, Any]:
    """Run make check and incorporate formatting changes"""
    
    # Materialize VFS to temp directory
    tmpdir = vfs.materialize_to_tempdir()
    
    results: dict[str, Any] = {
        "success": True,
        "formatted_files": [],
        "format_diffs": {},
        "typecheck_output": "",
        "typecheck_passed": False,
        "lint_output": "",
        "lint_passed": False,
    }
    
    try:
        # Capture file contents before formatting
        py_files = list(tmpdir.rglob("*.py"))
        before_format: dict[str, str] = {}
        for py_file in py_files:
            rel_path = str(py_file.relative_to(tmpdir))
            before_format[rel_path] = py_file.read_text()
        
        # Run make format
        format_result = subprocess.run(
            ["make", "format"],
            cwd=tmpdir,
            capture_output=True,
            text=True,
        )
        
        # Check which files changed and update VFS
        formatted_files = []
        format_diffs = {}
        for py_file in py_files:
            rel_path = str(py_file.relative_to(tmpdir))
            if not py_file.exists():
                continue
            after_content = py_file.read_text()
            before_content = before_format.get(rel_path, "")
            
            if after_content != before_content:
                formatted_files.append(rel_path)
                # Update VFS with formatted content
                vfs.write_file(rel_path, after_content)
                # Generate a simple diff summary
                format_diffs[rel_path] = _simple_diff(before_content, after_content)
        
        results["formatted_files"] = formatted_files
        results["format_diffs"] = format_diffs
        
        # Run make typecheck
        typecheck_result = subprocess.run(
            ["make", "typecheck"],
            cwd=tmpdir,
            capture_output=True,
            text=True,
        )
        results["typecheck_output"] = typecheck_result.stdout + typecheck_result.stderr
        results["typecheck_passed"] = typecheck_result.returncode == 0
        
        # Run make lint-check (not lint, since we already formatted)
        lint_result = subprocess.run(
            ["make", "lint-check"],
            cwd=tmpdir,
            capture_output=True,
            text=True,
        )
        results["lint_output"] = lint_result.stdout + lint_result.stderr
        results["lint_passed"] = lint_result.returncode == 0
        
        # Overall success
        results["success"] = results["typecheck_passed"] and results["lint_passed"]
        
        # Build summary message
        summary_parts = []
        if formatted_files:
            summary_parts.append(f"Formatted {len(formatted_files)} files: {', '.join(formatted_files)}")
        if results["typecheck_passed"]:
            summary_parts.append("✓ Type check passed")
        else:
            summary_parts.append("✗ Type check failed")
        if results["lint_passed"]:
            summary_parts.append("✓ Lint passed")
        else:
            summary_parts.append("✗ Lint failed")
        
        results["summary"] = "\n".join(summary_parts)
        
    finally:
        # Clean up temp directory
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)
    
    return results


def _simple_diff(before: str, after: str) -> str:
    """Generate a simple unified diff between two strings"""
    import difflib
    
    before_lines = before.splitlines(keepends=True)
    after_lines = after.splitlines(keepends=True)
    
    diff = difflib.unified_diff(
        before_lines,
        after_lines,
        fromfile="before",
        tofile="after",
        lineterm="",
    )
    
    return "".join(diff)
