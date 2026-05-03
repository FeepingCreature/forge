"""Execute inline commands extracted from streamed assistant text.

Pulled out of `LiveSession._start_inline_command_execution` so the
VFS-handoff dance + parse-check call site are testable as a plain
function.

This module is intentionally a thin wrapper around
`forge.tools.invocation.execute_inline_commands_with_parse_check`. The
only thing it owns is the `vfs.claim_thread()` / `release_thread()`
bracket so a worker thread can use the VFS safely. With SyncTaskRunner
those calls are no-ops; with QtTaskRunner they hand the VFS off from
the caller's thread to the worker thread for the duration of execution.
"""

from typing import Any

from forge.tools.invocation import (
    InlineCommand,
    execute_inline_commands_with_parse_check,
)


def run_inline_commands(
    vfs: Any,
    content: str,
    commands: list[InlineCommand],
) -> tuple[list[dict[str, Any]], int | None]:
    """Run all `commands` against `vfs`, honoring chain-stop-on-failure.

    Returns (results, failed_index) with the same semantics as
    `execute_inline_commands_with_parse_check`:
      - failed_index is None if every command succeeded.
      - failed_index is PARSE_CHECK_FAILED (-1) if the parse-check
        rejected the assistant text before any command ran.
      - failed_index >= 0 means commands[failed_index] failed and any
        later commands were skipped.
    """
    vfs.claim_thread()
    try:
        return execute_inline_commands_with_parse_check(vfs, content, commands)
    finally:
        vfs.release_thread()
