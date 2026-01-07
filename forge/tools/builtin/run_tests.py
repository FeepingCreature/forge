"""
Run the project's test suite on the current VFS state.

This tool:
1. Materializes the VFS to a temp directory
2. Discovers and runs the test command (make test, pytest, etc.)
3. Returns test output with pass/fail summary
"""

import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from forge.vfs.work_in_progress import WorkInProgressVFS


def get_schema() -> dict[str, Any]:
    """Return tool schema for LLM"""
    return {
        "type": "function",
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
    from forge.tools.side_effects import SideEffect

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
        # Capture file contents before running tests
        import contextlib

        all_files = list(tmpdir.rglob("*"))
        before_run: dict[str, str] = {}
        for file_path in all_files:
            if file_path.is_file():
                rel_path = str(file_path.relative_to(tmpdir))
                with contextlib.suppress(OSError, UnicodeDecodeError):
                    before_run[rel_path] = file_path.read_text(encoding="utf-8", errors="replace")

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
            results["passed"] = result.returncode == 0
            results["success"] = True

            # Generate summary
            if results["passed"]:
                results["summary"] = f"✓ Tests passed ({cmd_desc})"
            else:
                results["summary"] = f"✗ Tests failed ({cmd_desc})"
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
            results["success"] = True  # Tool succeeded, tests timed out

        except FileNotFoundError as e:
            results["output"] = f"Command not found: {e}"
            results["summary"] = f"✗ Could not run {cmd_desc}: command not found"

        # Check for file changes and write back to VFS
        changed_files = []
        all_files_after = list(tmpdir.rglob("*"))
        for file_path in all_files_after:
            if file_path.is_file():
                rel_path = str(file_path.relative_to(tmpdir))
                with contextlib.suppress(OSError, UnicodeDecodeError):
                    after_content = file_path.read_text(encoding="utf-8", errors="replace")
                    before_content = before_run.get(rel_path)

                    if before_content is None or after_content != before_content:
                        # File is new or changed - write back to VFS
                        vfs.write_file(rel_path, after_content)
                        changed_files.append(rel_path)

        if changed_files:
            results["changed_files"] = changed_files
            results["side_effects"] = [SideEffect.FILE_CHANGES]
            results["summary"] += f"\n\nFiles modified: {', '.join(changed_files)}"

    finally:
        # Clean up temp directory
        import shutil

        shutil.rmtree(tmpdir, ignore_errors=True)

    return results
