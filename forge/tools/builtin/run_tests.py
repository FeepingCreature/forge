"""
Run the project's test suite on the current VFS state.

This tool:
1. Materializes the VFS to a temp directory
2. Discovers and runs the test command (make test, pytest, etc.)
3. Returns test output with pass/fail summary
"""

import re
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from forge.tools.side_effects import SideEffect

if TYPE_CHECKING:
    from forge.vfs.work_in_progress import WorkInProgressVFS


# Pattern: <run_tests/> or <run_tests pattern="..." verbose="true"/>
_INLINE_PATTERN = re.compile(
    r'<run_tests(?:\s+pattern="([^"]*)")?(?:\s+verbose="(true|false)")?\s*/?>',
    re.DOTALL,
)


def get_inline_pattern() -> re.Pattern[str]:
    """Return compiled regex for inline invocation."""
    return _INLINE_PATTERN


def parse_inline_match(match: re.Match[str]) -> dict[str, Any]:
    """Parse regex match into tool arguments."""
    args: dict[str, Any] = {}
    if match.group(1):
        args["pattern"] = match.group(1)
    if match.group(2):
        args["verbose"] = match.group(2) == "true"
    return args


def get_schema() -> dict[str, Any]:
    """Return tool schema for LLM"""
    return {
        "type": "function",
        "invocation": "inline",
        "inline_syntax": '<run_tests/> or <run_tests pattern="test_foo" verbose="true"/>',
        "function": {
            "name": "run_tests",
            "description": """Run the project's test suite on the current VFS state.

This materializes your changes to a temp directory and runs tests.
Automatically discovers test command from:
- Makefile with 'test' target
- pytest (if pytest.ini, pyproject.toml with pytest, or test_*.py files exist)
- package.json scripts.test

Returns test output with pass/fail summary. Use this to verify your changes work.""",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Optional: only run tests matching this pattern (passed to pytest -k or similar)",
                    },
                    "verbose": {
                        "type": "boolean",
                        "description": "Show verbose test output (default: false)",
                        "default": False,
                    },
                },
            },
        },
    }


def _discover_test_command(tmpdir: Path) -> tuple[list[str], str]:
    """
    Discover the appropriate test command for this project.
    Returns (command_list, description).
    """
    # Check for Makefile with test target
    makefile = tmpdir / "Makefile"
    if makefile.exists():
        content = makefile.read_text()
        # Look for a test target (line starting with "test:")
        for line in content.splitlines():
            if line.startswith("test:") or line.startswith("test "):
                return ["make", "test"], "make test"

    # Check for pytest indicators
    has_pytest = False

    # Check pyproject.toml for pytest
    pyproject = tmpdir / "pyproject.toml"
    if pyproject.exists():
        content = pyproject.read_text()
        if "[tool.pytest" in content or "pytest" in content.lower():
            has_pytest = True

    # Check for pytest.ini
    if (tmpdir / "pytest.ini").exists():
        has_pytest = True

    # Check for conftest.py
    if (tmpdir / "conftest.py").exists():
        has_pytest = True

    # Check for test files
    test_files = list(tmpdir.rglob("test_*.py")) + list(tmpdir.rglob("*_test.py"))
    if test_files:
        has_pytest = True

    if has_pytest:
        return [sys.executable, "-m", "pytest"], "pytest"

    # Check for package.json with test script
    package_json = tmpdir / "package.json"
    if package_json.exists():
        import json

        try:
            pkg = json.loads(package_json.read_text())
            if "scripts" in pkg and "test" in pkg["scripts"]:
                return ["npm", "test"], "npm test"
        except json.JSONDecodeError:
            pass

    # Check for Cargo.toml (Rust)
    if (tmpdir / "Cargo.toml").exists():
        return ["cargo", "test"], "cargo test"

    # Check for go.mod (Go)
    if (tmpdir / "go.mod").exists():
        return ["go", "test", "./..."], "go test"

    # Default: try pytest anyway
    return [sys.executable, "-m", "pytest"], "pytest (default)"


def execute(vfs: "WorkInProgressVFS", args: dict[str, Any]) -> dict[str, Any]:
    """Run tests and return results"""
    pattern = args.get("pattern", "")
    verbose = args.get("verbose", False)

    # Materialize VFS to temp directory
    tmpdir = vfs.materialize_to_tempdir()

    results: dict[str, Any] = {
        "success": False,
        "passed": False,
        "test_command": "",
        "output": "",
        "summary": "",
    }

    try:
        # Discover test command
        cmd, cmd_desc = _discover_test_command(tmpdir)
        results["test_command"] = cmd_desc

        # Add pattern filter if specified
        if pattern:
            if "pytest" in cmd_desc:
                cmd.extend(["-k", pattern])
            elif cmd_desc == "make test":
                # Can't easily filter make test, note it
                results["note"] = (
                    "Pattern filtering not supported with make test, running all tests"
                )

        # Add verbose flag
        if verbose:
            if "pytest" in cmd_desc:
                cmd.append("-v")
            elif "cargo" in cmd_desc:
                cmd.append("--verbose")

        # Run tests with timeout
        try:
            result = subprocess.run(
                cmd,
                cwd=tmpdir,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=300,  # 5 minute timeout
            )

            output = result.stdout
            if result.stderr:
                output += "\n--- stderr ---\n" + result.stderr

            results["output"] = output
            results["success"] = result.returncode == 0

            # Generate summary
            if results["success"]:
                results["summary"] = f"✓ Tests passed ({cmd_desc})"
            else:
                results["summary"] = f"✗ Tests failed ({cmd_desc})"
                results["error"] = output  # Include full output as error
                # Try to extract failure count from pytest output
                if "pytest" in cmd_desc:
                    for line in output.splitlines():
                        if "failed" in line.lower() and (
                            "passed" in line.lower() or "error" in line.lower()
                        ):
                            results["summary"] += f"\n{line.strip()}"
                            break

        except subprocess.TimeoutExpired:
            results["output"] = "Test run timed out after 5 minutes"
            results["summary"] = "✗ Tests timed out"
            results["error"] = "Test run timed out after 5 minutes"
            results["success"] = False

        except FileNotFoundError as e:
            results["output"] = f"Command not found: {e}"
            results["summary"] = f"✗ Could not run {cmd_desc}: command not found"

        # Write all text files back to VFS and track which ones changed
        modified_files = []
        for rel_path in vfs.list_files():
            file_path = tmpdir / rel_path
            if file_path.exists():
                new_content = file_path.read_text(encoding="utf-8")
                # Check if content actually changed
                try:
                    old_content = vfs.read_file(rel_path)
                    if new_content != old_content:
                        vfs.write_file(rel_path, new_content)
                        modified_files.append(rel_path)
                except (FileNotFoundError, KeyError):
                    # File is new
                    vfs.write_file(rel_path, new_content)
                    modified_files.append(rel_path)

        # Declare side effects
        side_effects = []

        if modified_files:
            results["modified_files"] = modified_files
            side_effects.append(SideEffect.FILES_MODIFIED)

        # Always provide display output for the UI (test results are useful to see)
        results["display_output"] = results.get("output", results.get("summary", ""))
        side_effects.append(SideEffect.HAS_DISPLAY_OUTPUT)

        if side_effects:
            results["side_effects"] = side_effects

    finally:
        # Clean up temp directory
        import shutil

        shutil.rmtree(tmpdir, ignore_errors=True)

    return results
