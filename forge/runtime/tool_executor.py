"""Execute a batch of tool calls sequentially.

Pulled out of `LiveSession._execute_tool_calls` so the per-tool dispatch
loop — including JSON-argument parsing, the doubly-encoded-string
unwrap, the chain-stop-on-failure rule, and the
ToolStarted/ToolFinished event emission — is testable as a plain
function.

VFS hand-off (`claim_thread` / `release_thread`) is owned here, same as
`run_inline_commands`. With SyncTaskRunner those are no-ops; with
QtTaskRunner they bracket the worker-thread access window.

The result list mirrors what `LiveSession._on_tools_all_finished`
expects: each entry is
    {"tool_call": <orig dict>, "args": <parsed dict>, "result": <dict>}
plus an optional "parse_error": True flag when the arguments couldn't
be parsed as JSON.
"""

import json
from typing import Any

from forge.runtime.events import ToolFinished, ToolStarted


def execute_tool_calls(
    tool_calls: list[dict[str, Any]],
    tool_manager: Any,
    session_manager: Any,
    emit: Any,
) -> list[dict[str, Any]]:
    """Run `tool_calls` one-by-one through `tool_manager.execute_tool`.

    Stops at the first tool whose result is `{"success": False}` (or
    where argument parsing failed). Per-tool ToolStarted / ToolFinished
    events are emitted via `emit` so the UI can update live.
    """
    results: list[dict[str, Any]] = []

    session_manager.vfs.claim_thread()
    try:
        for tool_call in tool_calls:
            tool_name = tool_call["function"]["name"]
            arguments_str = tool_call["function"]["arguments"]
            tool_call_id = tool_call.get("id", "")

            # Parse arguments. Mirror the legacy ToolExecutionWorker
            # semantics exactly:
            #   - empty string → empty dict (some tools take no args).
            #   - top-level invalid JSON → wrap raw under INVALID_JSON,
            #     synthesize a failure result, ToolFinished + break.
            #   - string value that looks like a JSON list/object →
            #     try a second decode and use the parsed value if it
            #     comes back as list/dict (LLMs sometimes double-encode
            #     nested structures by accident).
            try:
                tool_args: dict[str, Any] = json.loads(arguments_str) if arguments_str else {}
                for key, value in tool_args.items():
                    if isinstance(value, str) and value.startswith(("[", "{")):
                        try:
                            parsed = json.loads(value)
                            if isinstance(parsed, (list, dict)):
                                tool_args[key] = parsed
                        except json.JSONDecodeError:
                            pass
            except json.JSONDecodeError as e:
                tool_args = {"INVALID_JSON": arguments_str}
                result = {"success": False, "error": f"Invalid JSON arguments: {e}"}
                emit(ToolFinished(tool_call_id, tool_name, tool_args, result))
                results.append(
                    {
                        "tool_call": tool_call,
                        "args": tool_args,
                        "result": result,
                        "parse_error": True,
                    }
                )
                break

            emit(ToolStarted(tool_name, tool_args))
            result = tool_manager.execute_tool(tool_name, tool_args, session_manager)
            emit(ToolFinished(tool_call_id, tool_name, tool_args, result))
            results.append({"tool_call": tool_call, "args": tool_args, "result": result})

            # Chain-stop-on-failure: a tool that explicitly returns
            # success=False aborts the rest of the batch. Tools that
            # omit the field default to True (best-effort assumption).
            if not result.get("success", True):
                break
    finally:
        session_manager.vfs.release_thread()

    return results
