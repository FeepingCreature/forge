"""
Background worker classes for AI chat widget.

These QObject workers run in separate threads to handle:
- Repository summary generation
- Streaming LLM responses
- Inline command execution
- Tool call execution
"""

import json
from typing import TYPE_CHECKING, Any

from PySide6.QtCore import QObject, Signal

if TYPE_CHECKING:
    from forge.llm.client import LLMClient
    from forge.session.manager import SessionManager


class SummaryWorker(QObject):
    """Worker for generating repository summaries in background"""

    finished = Signal(int)  # Emitted with number of summaries generated
    error = Signal(str)  # Emitted on error
    progress = Signal(int, int, str)  # Emitted for progress (current, total, filepath)

    def __init__(self, session_manager: "SessionManager", force_refresh: bool = False) -> None:
        super().__init__()
        self.session_manager = session_manager
        self.force_refresh = force_refresh

    def run(self) -> None:
        """Generate summaries"""
        try:
            self.session_manager.generate_repo_summaries(
                force_refresh=self.force_refresh,
                progress_callback=lambda cur, total, fp: self.progress.emit(cur, total, fp),
            )
            count = len(self.session_manager.repo_summaries)
            self.finished.emit(count)
        except Exception as e:
            import traceback

            print(f"❌ SummaryWorker error: {e}")
            traceback.print_exc()
            self.error.emit(str(e))


class StreamWorker(QObject):
    """Worker for handling streaming LLM responses in a separate thread"""

    chunk_received = Signal(str)  # Emitted for each text chunk
    tool_call_delta = Signal(int, dict)  # Emitted for tool call updates (index, current_state)
    finished = Signal(dict)  # Emitted when stream is complete
    error = Signal(str)  # Emitted on error

    def __init__(
        self,
        client: "LLMClient",
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
    ) -> None:
        super().__init__()
        self.client = client
        self.messages = messages
        self.tools = tools
        self.current_content = ""
        self.current_tool_calls: list[dict[str, Any]] = []

    def run(self) -> None:
        """Run the streaming request"""
        try:
            for chunk in self.client.chat_stream(self.messages, self.tools):
                if "choices" not in chunk or not chunk["choices"]:
                    continue

                delta = chunk["choices"][0].get("delta", {})

                # Handle content chunks
                if "content" in delta and delta["content"]:
                    content = delta["content"]
                    self.current_content += content
                    self.chunk_received.emit(content)

                # Handle tool calls
                if "tool_calls" in delta:
                    for tool_call_delta in delta["tool_calls"]:
                        index = tool_call_delta.get("index", 0)

                        # Ensure we have enough tool calls in the list
                        while len(self.current_tool_calls) <= index:
                            self.current_tool_calls.append(
                                {
                                    "id": "",
                                    "type": "function",
                                    "function": {"name": "", "arguments": ""},
                                }
                            )

                        # Update tool call
                        if "id" in tool_call_delta:
                            self.current_tool_calls[index]["id"] = tool_call_delta["id"]
                        if "function" in tool_call_delta:
                            func = tool_call_delta["function"]
                            if "name" in func:
                                self.current_tool_calls[index]["function"]["name"] = func["name"]
                            if "arguments" in func:
                                self.current_tool_calls[index]["function"]["arguments"] += func[
                                    "arguments"
                                ]

                        # Emit delta with current state of this tool call
                        self.tool_call_delta.emit(index, self.current_tool_calls[index].copy())

            # Emit final result
            result = {
                "content": self.current_content if self.current_content else None,
                "tool_calls": self.current_tool_calls if self.current_tool_calls else None,
            }
            self.finished.emit(result)

        except Exception as e:
            import traceback

            print(f"❌ StreamWorker error (LLM): {e}")
            traceback.print_exc()
            self.error.emit(str(e))


class InlineCommandWorker(QObject):
    """Worker for executing inline commands in a background thread.

    Executes commands like <edit>, <run_tests/>, <check/>, <commit/> sequentially.
    If any command fails, remaining commands are aborted.

    Claims VFS thread ownership while running, releases when done.
    """

    finished = Signal(list, object)  # Emitted when done (results, failed_index or None)
    error = Signal(str)  # Emitted on error

    def __init__(self, vfs: Any, commands: list) -> None:
        super().__init__()
        self.vfs = vfs
        self.commands = commands

    def run(self) -> None:
        """Execute inline commands sequentially."""
        from forge.tools.invocation import execute_inline_commands

        self.vfs.claim_thread()
        try:
            results, failed_index = execute_inline_commands(self.vfs, self.commands)
            self.finished.emit(results, failed_index)
        except Exception as e:
            import traceback

            print(f"❌ InlineCommandWorker error: {e}")
            traceback.print_exc()
            self.error.emit(str(e))
        finally:
            self.vfs.release_thread()


class ToolExecutionWorker(QObject):
    """Worker for executing tool calls in a background thread.

    Executes tools SEQUENTIALLY as a pipeline. If any tool fails (success=False),
    remaining tools are aborted and marked as such. This allows the AI to chain
    tools like: search_replace → search_replace → check → commit → done

    If all tools succeed, the chain completes. If any fails, the AI gets control
    back to handle the error.

    Claims VFS thread ownership while running, releases when done.
    """

    tool_started = Signal(str, dict)  # Emitted when a tool starts (name, args)
    tool_finished = Signal(
        str, str, dict, dict
    )  # Emitted when a tool finishes (tool_call_id, name, args, result)
    all_finished = Signal(list)  # Emitted when all tools complete (list of results)
    error = Signal(str)  # Emitted on error

    def __init__(
        self,
        tool_calls: list[dict[str, Any]],
        tool_manager: Any,
        session_manager: Any,
    ) -> None:
        super().__init__()
        self.tool_calls = tool_calls
        self.tool_manager = tool_manager
        self.session_manager = session_manager
        self.results: list[dict[str, Any]] = []

    def run(self) -> None:
        """Execute tool calls sequentially, stopping on first failure.

        When a tool fails, we stop immediately and don't process remaining tools.
        The AI will only see the failed tool's result (not the unattempted ones),
        allowing it to fix the issue and resubmit the full chain.
        """
        # Claim VFS for this thread
        self.session_manager.vfs.claim_thread()

        try:
            for tool_call in self.tool_calls:
                tool_name = tool_call["function"]["name"]
                arguments_str = tool_call["function"]["arguments"]
                tool_call_id = tool_call.get("id", "")

                # Parse arguments
                # With fine-grained tool streaming, we might get invalid JSON
                # In that case, wrap it so the model sees what it produced
                try:
                    tool_args = json.loads(arguments_str) if arguments_str else {}

                    # Fix doubly-encoded JSON: if a string value is itself valid JSON,
                    # parse it. This handles cases like {"branches": "[\"branch-name\"]"}
                    # where the LLM incorrectly stringified an array.
                    for key, value in tool_args.items():
                        if isinstance(value, str) and value.startswith(("[", "{")):
                            try:
                                parsed = json.loads(value)
                                if isinstance(parsed, (list, dict)):
                                    tool_args[key] = parsed
                            except json.JSONDecodeError:
                                pass  # Not valid JSON, keep as string

                except json.JSONDecodeError as e:
                    # Wrap invalid JSON so it gets sent back to the model
                    tool_args = {"INVALID_JSON": arguments_str}
                    result = {"success": False, "error": f"Invalid JSON arguments: {e}"}
                    self.tool_finished.emit(tool_call_id, tool_name, tool_args, result)
                    self.results.append(
                        {
                            "tool_call": tool_call,
                            "args": tool_args,
                            "result": result,
                            "parse_error": True,
                        }
                    )
                    # Stop here - don't process remaining tools, don't record them
                    break

                # Emit that we're starting this tool
                self.tool_started.emit(tool_name, tool_args)

                # Execute tool
                result = self.tool_manager.execute_tool(tool_name, tool_args, self.session_manager)

                # Emit result with tool_call_id
                self.tool_finished.emit(tool_call_id, tool_name, tool_args, result)

                self.results.append(
                    {
                        "tool_call": tool_call,
                        "args": tool_args,
                        "result": result,
                    }
                )

                # Check if this tool failed - stop processing remaining tools
                # Tools signal failure by returning success=False
                # The AI will only see this failed result (not unattempted ones)
                if not result.get("success", True):
                    break

            self.all_finished.emit(self.results)

        except Exception as e:
            import traceback

            print(f"❌ ToolExecutionWorker error: {e}")
            traceback.print_exc()
            self.error.emit(str(e))
        finally:
            # Always release VFS ownership
            self.session_manager.vfs.release_thread()
