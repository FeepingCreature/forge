"""
SessionRunner - Headless AI session execution engine.

This class owns the AI run loop (stream → tools → repeat) and can operate
with or without a UI attached. AIChatWidget becomes a view that attaches
to a SessionRunner to render and interact.

Key concepts:
- SessionRunner owns: messages, streaming state, worker threads
- AIChatWidget attaches/detaches without disrupting execution
- Spawned child sessions run headlessly until user attaches a view

Attach/Detach model:
- When detached, events buffer up in a thread-safe queue
- On attach: lock buffer, get snapshot, unlock, replay buffered events
- Once buffer is drained, switch to direct signal emission
"""

from collections import deque
from threading import Lock
from typing import TYPE_CHECKING, Any, Protocol

from PySide6.QtCore import QObject, QThread, Signal

if TYPE_CHECKING:
    from forge.session.manager import SessionManager


# Event types for buffering
class SessionEvent:
    """Base class for buffered session events."""
    pass


class ChunkEvent(SessionEvent):
    """Streaming text chunk."""
    def __init__(self, chunk: str):
        self.chunk = chunk


class ToolCallDeltaEvent(SessionEvent):
    """Streaming tool call update."""
    def __init__(self, index: int, tool_call: dict[str, Any]):
        self.index = index
        self.tool_call = tool_call


class ToolStartedEvent(SessionEvent):
    """Tool execution started."""
    def __init__(self, tool_name: str, tool_args: dict[str, Any]):
        self.tool_name = tool_name
        self.tool_args = tool_args


class ToolFinishedEvent(SessionEvent):
    """Tool execution finished."""
    def __init__(self, tool_call_id: str, tool_name: str, tool_args: dict[str, Any], result: dict[str, Any]):
        self.tool_call_id = tool_call_id
        self.tool_name = tool_name
        self.tool_args = tool_args
        self.result = result


class StateChangedEvent(SessionEvent):
    """Session state changed."""
    def __init__(self, state: str):
        self.state = state


class TurnFinishedEvent(SessionEvent):
    """AI turn completed."""
    def __init__(self, commit_oid: str):
        self.commit_oid = commit_oid


class ErrorEvent(SessionEvent):
    """Error occurred."""
    def __init__(self, error: str):
        self.error = error


class MessageAddedEvent(SessionEvent):
    """Message added."""
    def __init__(self, message: dict[str, Any]):
        self.message = message


class MessageUpdatedEvent(SessionEvent):
    """Message updated."""
    def __init__(self, index: int, message: dict[str, Any]):
        self.index = index
        self.message = message


class MessagesTruncatedEvent(SessionEvent):
    """Messages truncated."""
    def __init__(self, new_length: int):
        self.new_length = new_length


class SessionRunnerDelegate(Protocol):
    """Protocol for objects that want to observe/interact with a SessionRunner.
    
    AIChatWidget implements this to receive updates and render them.
    A headless runner can have no delegate (or a minimal logging one).
    """
    
    def on_stream_chunk(self, chunk: str) -> None:
        """Called for each streaming text chunk."""
        ...
    
    def on_tool_call_delta(self, index: int, tool_call: dict[str, Any]) -> None:
        """Called for streaming tool call updates."""
        ...
    
    def on_tool_started(self, tool_name: str, tool_args: dict[str, Any]) -> None:
        """Called when a tool starts executing."""
        ...
    
    def on_tool_finished(
        self, tool_call_id: str, tool_name: str, tool_args: dict[str, Any], result: dict[str, Any]
    ) -> None:
        """Called when a tool finishes executing."""
        ...
    
    def on_turn_finished(self, commit_oid: str) -> None:
        """Called when the AI turn completes."""
        ...
    
    def on_error(self, error: str) -> None:
        """Called on errors."""
        ...
    
    def on_state_changed(self, state: "SessionState") -> None:
        """Called when session state changes."""
        ...
    
    def needs_tool_approval(self, tool_name: str, tool_info: dict[str, Any]) -> None:
        """Called when a tool needs user approval. Blocks until resolved."""
        ...


class SessionState:
    """Session execution state."""
    
    IDLE = "idle"              # Not running, ready for input
    RUNNING = "running"        # Actively processing (streaming or executing tools)
    WAITING_APPROVAL = "waiting_approval"  # Blocked on tool approval
    WAITING_INPUT = "waiting_input"        # AI asked a question (done() called)
    WAITING_CHILDREN = "waiting_children"  # Blocked on wait_session()
    COMPLETED = "completed"    # Session finished (done() with no question)
    ERROR = "error"            # Unrecoverable error


class SessionRunner(QObject):
    """
    Headless AI session execution engine.
    
    Owns the run loop and can operate with or without an attached UI.
    Multiple SessionRunners can exist simultaneously (one per active session).
    
    Signals are used for thread-safe communication with optional UI.
    If no UI is attached, signals simply aren't connected.
    
    This is the authoritative owner of:
    - messages: The conversation history
    - streaming_content: Current streaming text accumulator
    - streaming_tool_calls: Current streaming tool calls
    - is_streaming: Whether we're currently streaming
    """
    
    # Signals for UI attachment (optional - headless runs ignore these)
    chunk_received = Signal(str)
    tool_call_delta = Signal(int, dict)
    tool_started = Signal(str, dict)
    tool_finished = Signal(str, str, dict, dict)  # id, name, args, result
    turn_finished = Signal(str)  # commit_oid
    error_occurred = Signal(str)
    state_changed = Signal(str)  # SessionState value
    
    # Signal for messages list changes (for UI sync)
    message_added = Signal(dict)  # The message that was added
    message_updated = Signal(int, dict)  # index, updated message
    messages_truncated = Signal(int)  # new length after truncation
    
    # Signal for tool approval (requires UI interaction)
    approval_needed = Signal(str, dict)  # tool_name, tool_info
    
    def __init__(
        self,
        session_manager: "SessionManager",
        messages: list[dict[str, Any]] | None = None,
    ) -> None:
        super().__init__()
        self.session_manager = session_manager
        
        # === AUTHORITATIVE SESSION STATE ===
        # These are the source of truth - UI reads from here
        self.messages: list[dict[str, Any]] = messages or []
        self.streaming_content: str = ""
        self.streaming_tool_calls: list[dict[str, Any]] = []
        self.is_streaming: bool = False
        
        # === ATTACH/DETACH STATE ===
        self._attached = False
        self._event_buffer: deque[SessionEvent] = deque()
        self._buffer_lock = Lock()
        
        # Execution state
        self._state = SessionState.IDLE
        self._cancel_requested = False
        
        # Tool execution tracking (across all batches in a turn)
        self._turn_executed_tool_ids: set[str] = set()
        
        # Queued message (injected mid-turn)
        self._queued_message: str | None = None
        
        # Pending file updates (applied after tool results recorded)
        self._pending_file_updates: list[tuple[str, str | None]] = []
        
        # Newly created files (for summary generation)
        self._newly_created_files: set[str] = set()
        
        # Worker threads
        self._stream_thread: QThread | None = None
        self._stream_worker: Any = None  # StreamWorker
        self._tool_thread: QThread | None = None
        self._tool_worker: Any = None  # ToolExecutionWorker
        self._inline_thread: QThread | None = None
        self._inline_worker: Any = None  # InlineCommandWorker
        
        # Child session tracking (for spawn/wait)
        self._child_sessions: list[str] = []  # Branch names
        self._parent_session: str | None = None
        self._yield_message: str | None = None  # Message from done() call
    
    @property
    def state(self) -> str:
        """Current session state."""
        return self._state
    
    @state.setter
    def state(self, value: str) -> None:
        """Set state and emit event."""
        if self._state != value:
            self._state = value
            self._emit_event(StateChangedEvent(value))
    
    @property
    def is_running(self) -> bool:
        """Check if session is actively processing."""
        return self._state == SessionState.RUNNING
    
    # === MESSAGE MANAGEMENT ===
    # All message mutations go through these methods to ensure signals are emitted
    
    def add_message(self, message: dict[str, Any]) -> int:
        """Add a message and emit event. Returns the index."""
        self.messages.append(message)
        self._emit_event(MessageAddedEvent(message))
        return len(self.messages) - 1
    
    def update_message(self, index: int, updates: dict[str, Any]) -> None:
        """Update a message at index and emit event."""
        if 0 <= index < len(self.messages):
            self.messages[index].update(updates)
            self._emit_event(MessageUpdatedEvent(index, self.messages[index]))
    
    def update_last_assistant_message(self, updates: dict[str, Any]) -> None:
        """Update the last assistant message (common pattern during streaming)."""
        for i in range(len(self.messages) - 1, -1, -1):
            if self.messages[i].get("role") == "assistant":
                self.update_message(i, updates)
                return
    
    def pop_last_message(self) -> dict[str, Any] | None:
        """Remove and return the last message."""
        if self.messages:
            msg = self.messages.pop()
            self._emit_event(MessagesTruncatedEvent(len(self.messages)))
            return msg
        return None
    
    def truncate_messages(self, new_length: int) -> None:
        """Truncate messages to new_length."""
        if new_length < len(self.messages):
            self.messages = self.messages[:new_length]
            self._emit_event(MessagesTruncatedEvent(new_length))
    
    # === STREAMING STATE ===
    
    def start_streaming(self) -> None:
        """Start a new streaming response."""
        self.streaming_content = ""
        self.streaming_tool_calls = []
        self.is_streaming = True
        # Add placeholder message
        self.add_message({"role": "assistant", "content": ""})
    
    def append_streaming_chunk(self, chunk: str) -> None:
        """Append a chunk to streaming content."""
        self.streaming_content += chunk
        # Update the last message
        self.update_last_assistant_message({"content": self.streaming_content})
        # Emit for real-time UI update
        self._emit_event(ChunkEvent(chunk))
    
    def update_streaming_tool_call(self, index: int, tool_call: dict[str, Any]) -> None:
        """Update a streaming tool call."""
        while len(self.streaming_tool_calls) <= index:
            self.streaming_tool_calls.append({})
        self.streaming_tool_calls[index] = tool_call
        self._emit_event(ToolCallDeltaEvent(index, tool_call))
    
    def finish_streaming(self, content: str | None, tool_calls: list[dict[str, Any]] | None) -> None:
        """Finalize streaming with final content and tool_calls."""
        self.is_streaming = False
        updates: dict[str, Any] = {}
        if content:
            updates["content"] = content
        if tool_calls:
            updates["tool_calls"] = tool_calls
        if updates:
            self.update_last_assistant_message(updates)
        self.streaming_tool_calls = []
    
    # === ATTACH/DETACH API ===
    
    def attach(self) -> tuple[list[dict[str, Any]], str, list[dict[str, Any]], bool, str]:
        """
        Attach a UI to this runner.
        
        Returns a snapshot of current state for immediate rendering:
        (messages, streaming_content, streaming_tool_calls, is_streaming, state)
        
        After calling attach(), connect to signals and call drain_buffer()
        to replay any events that occurred during the attach process.
        """
        with self._buffer_lock:
            self._attached = True
            # Return snapshot of current state
            return (
                list(self.messages),  # Copy to avoid mutation during iteration
                self.streaming_content,
                list(self.streaming_tool_calls),
                self.is_streaming,
                self._state,
            )
    
    def detach(self) -> None:
        """
        Detach UI from this runner.
        
        Events will buffer until another UI attaches.
        """
        with self._buffer_lock:
            self._attached = False
            self._event_buffer.clear()  # Clear any stale events
    
    def drain_buffer(self) -> list[SessionEvent]:
        """
        Drain and return all buffered events.
        
        Call this after attach() and connecting signals to replay
        any events that occurred during the attach process.
        
        Returns list of events in order they occurred.
        """
        with self._buffer_lock:
            events = list(self._event_buffer)
            self._event_buffer.clear()
            return events
    
    def _emit_event(self, event: SessionEvent) -> None:
        """
        Emit an event - either directly via signal or buffer if detached.
        
        This is the central dispatch point for all events.
        """
        with self._buffer_lock:
            if not self._attached:
                self._event_buffer.append(event)
                return
        
        # Attached - emit directly via appropriate signal
        if isinstance(event, ChunkEvent):
            self.chunk_received.emit(event.chunk)
        elif isinstance(event, ToolCallDeltaEvent):
            self.tool_call_delta.emit(event.index, event.tool_call)
        elif isinstance(event, ToolStartedEvent):
            self.tool_started.emit(event.tool_name, event.tool_args)
        elif isinstance(event, ToolFinishedEvent):
            self.tool_finished.emit(event.tool_call_id, event.tool_name, event.tool_args, event.result)
        elif isinstance(event, StateChangedEvent):
            self.state_changed.emit(event.state)
        elif isinstance(event, TurnFinishedEvent):
            self.turn_finished.emit(event.commit_oid)
        elif isinstance(event, ErrorEvent):
            self.error_occurred.emit(event.error)
        elif isinstance(event, MessageAddedEvent):
            self.message_added.emit(event.message)
        elif isinstance(event, MessageUpdatedEvent):
            self.message_updated.emit(event.index, event.message)
        elif isinstance(event, MessagesTruncatedEvent):
            self.messages_truncated.emit(event.new_length)
    
    # === PUBLIC API ===
    
    def send_message(self, text: str) -> bool:
        """
        Send a user message to the AI.
        
        Returns True if message was accepted, False if session is busy.
        If busy but running, the message is queued for after current operation.
        """
        if self._state == SessionState.RUNNING:
            # Queue the message
            self._queued_message = text
            return True
        
        if self._state not in (SessionState.IDLE, SessionState.WAITING_INPUT):
            return False
        
        # Add message to conversation
        self.add_message({"role": "user", "content": text})
        self.session_manager.append_user_message(text)
        
        # Reset turn tracking
        self._turn_executed_tool_ids = set()
        self._cancel_requested = False
        
        # Start processing
        self.state = SessionState.RUNNING
        self._process_llm_request()
        
        return True
    
    def cancel(self) -> None:
        """Cancel the current operation."""
        if self._state != SessionState.RUNNING:
            return
        
        self._cancel_requested = True
        
        # Clean up threads
        self._cleanup_threads()
        
        # Discard pending VFS changes
        self.session_manager.tool_manager.vfs = self.session_manager._create_fresh_vfs()
        
        # Remove incomplete assistant message
        if self.messages and self.messages[-1].get("role") == "assistant":
            self.pop_last_message()
        
        self.is_streaming = False
        self.streaming_content = ""
        self.streaming_tool_calls = []
        
        self.state = SessionState.IDLE
        self._emit_event(ErrorEvent("Cancelled by user"))
    
    def _cleanup_threads(self) -> None:
        """Clean up all worker threads."""
        for thread_attr, worker_attr in [
            ("_stream_thread", "_stream_worker"),
            ("_tool_thread", "_tool_worker"),
            ("_inline_thread", "_inline_worker"),
        ]:
            thread = getattr(self, thread_attr)
            if thread and thread.isRunning():
                thread.quit()
                thread.wait(3000)
                if thread.isRunning():
                    thread.terminate()
            setattr(self, thread_attr, None)
            setattr(self, worker_attr, None)
    
    def _process_llm_request(self) -> None:
        """Start an LLM request with streaming."""
        from forge.llm.client import LLMClient
        from forge.ui.ai_chat_widget import StreamWorker
        
        api_key = self.session_manager.settings.get_api_key()
        model = self.session_manager.settings.get("llm.model", "anthropic/claude-3.5-sonnet")
        client = LLMClient(api_key, model)
        
        # Build prompt
        self.session_manager.sync_prompt_manager()
        messages = self.session_manager.get_prompt_messages()
        tools = self.session_manager.tool_manager.discover_tools()
        
        # Start streaming
        self.start_streaming()
        
        # Start streaming in thread
        self._stream_thread = QThread()
        self._stream_worker = StreamWorker(client, messages, tools or None)
        self._stream_worker.moveToThread(self._stream_thread)
        
        # Connect signals
        self._stream_worker.chunk_received.connect(self._on_stream_chunk)
        self._stream_worker.tool_call_delta.connect(self._on_tool_call_delta)
        self._stream_worker.finished.connect(self._on_stream_finished)
        self._stream_worker.error.connect(self._on_stream_error)
        self._stream_thread.started.connect(self._stream_worker.run)
        
        self._stream_thread.start()
    
    def _on_stream_chunk(self, chunk: str) -> None:
        """Handle streaming text chunk."""
        self.append_streaming_chunk(chunk)
    
    def _on_tool_call_delta(self, index: int, tool_call: dict[str, Any]) -> None:
        """Handle streaming tool call update."""
        self.update_streaming_tool_call(index, tool_call)
    
    def _on_stream_finished(self, result: dict[str, Any]) -> None:
        """Handle stream completion."""
        # Clean up thread
        if self._stream_thread:
            self._stream_thread.quit()
            self._stream_thread.wait()
            self._stream_thread = None
            self._stream_worker = None
        
        # Finalize streaming
        self.finish_streaming(result.get("content"), result.get("tool_calls"))
        
        # Process inline commands first
        if result.get("content"):
            from forge.tools.invocation import parse_inline_commands
            commands = parse_inline_commands(result["content"])
            if commands:
                self._pending_stream_result = result
                self._start_inline_command_execution(commands)
                return
        
        # Continue with tool calls or finish
        self._finish_stream_processing(result)
    
    def _on_stream_error(self, error_msg: str) -> None:
        """Handle streaming error."""
        if self._stream_thread:
            self._stream_thread.quit()
            self._stream_thread.wait()
            self._stream_thread = None
            self._stream_worker = None
        
        self.is_streaming = False
        self.streaming_content = ""
        self.streaming_tool_calls = []
        
        # Remove empty assistant message
        if (
            self.messages
            and self.messages[-1].get("role") == "assistant"
            and not self.messages[-1].get("content")
        ):
            self.pop_last_message()
        
        # Feed error back to conversation
        error_content = f"**Error from LLM provider:**\n\n```\n{error_msg}\n```"
        self.add_message({"role": "user", "content": error_content})
        self.session_manager.append_user_message(error_content)
        
        # Retry
        self._process_llm_request()
    
    def _start_inline_command_execution(self, commands: list) -> None:
        """Start executing inline commands in background thread."""
        from forge.ui.ai_chat_widget import InlineCommandWorker
        
        self._inline_thread = QThread()
        self._inline_worker = InlineCommandWorker(self.session_manager.vfs, commands)
        self._inline_worker.moveToThread(self._inline_thread)
        
        self._inline_worker.finished.connect(self._on_inline_commands_finished)
        self._inline_worker.error.connect(self._on_inline_commands_error)
        self._inline_thread.started.connect(self._inline_worker.run)
        
        self._pending_inline_commands = commands
        self._inline_thread.start()
    
    def _on_inline_commands_finished(self, results: list, failed_index: int | None) -> None:
        """Handle inline command completion."""
        if self._inline_thread:
            self._inline_thread.quit()
            self._inline_thread.wait()
        self._inline_thread = None
        self._inline_worker = None
        
        commands = getattr(self, "_pending_inline_commands", [])
        result = getattr(self, "_pending_stream_result", {})
        content = result.get("content", "")
        
        if failed_index is not None:
            # Handle failure - truncate and continue with error
            failed_cmd = commands[failed_index]
            truncated_content = content[: failed_cmd.end_pos]
            
            self.update_last_assistant_message({"content": truncated_content})
            # Remove tool_calls if present
            for i in range(len(self.messages) - 1, -1, -1):
                if self.messages[i].get("role") == "assistant":
                    self.messages[i].pop("tool_calls", None)
                    break
            
            error_result = results[failed_index]
            error_msg = error_result.get("error", "Unknown error")
            
            # Process successful commands before failure
            for i, res in enumerate(results[:-1]):
                if res.get("success"):
                    self._process_tool_side_effects(res, commands[i])
            
            # Add error to conversation
            error_content = f"❌ `{failed_cmd.tool_name}` failed:\n\n{error_msg}"
            self.session_manager.append_assistant_message(truncated_content)
            self.add_message({"role": "user", "content": error_content, "_ui_only": True})
            self.session_manager.append_user_message(error_content)
            
            # Continue so AI can fix
            self._continue_after_tools()
            return
        
        # All succeeded
        for i, res in enumerate(results):
            self._process_tool_side_effects(res, commands[i])
        
        self.update_last_assistant_message({"_inline_results": results})
        
        # Build success feedback
        success_parts = []
        for i, cmd in enumerate(commands):
            res = results[i]
            if cmd.tool_name == "run_tests":
                summary = res.get("summary", "✓ Tests passed")
                success_parts.append(f"✓ run_tests: {summary}")
            elif cmd.tool_name == "check":
                summary = res.get("summary", "All checks passed")
                success_parts.append(f"✓ check: {summary}")
            elif cmd.tool_name == "commit":
                commit_oid = res.get("commit", "")[:8]
                success_parts.append(f"✓ commit: {commit_oid}")
            else:
                success_parts.append(f"✓ {cmd.tool_name}")
        
        self.session_manager.append_user_message(
            "Commands executed:\n" + "\n".join(success_parts)
        )
        
        self._finish_stream_processing(result)
    
    def _on_inline_commands_error(self, error_msg: str) -> None:
        """Handle inline command execution error."""
        if self._inline_thread:
            self._inline_thread.quit()
            self._inline_thread.wait()
        self._inline_thread = None
        self._inline_worker = None
        
        self.state = SessionState.ERROR
        self._emit_event(ErrorEvent(f"Inline command error: {error_msg}"))
    
    def _process_tool_side_effects(self, result: dict[str, Any], cmd: Any = None) -> None:
        """Process side effects from tool execution."""
        from forge.tools.side_effects import SideEffect
        
        side_effects = result.get("side_effects", [])
        
        if SideEffect.FILES_MODIFIED in side_effects:
            for filepath in result.get("modified_files", []):
                self._pending_file_updates.append((filepath, None))
        
        if SideEffect.NEW_FILES_CREATED in side_effects:
            for filepath in result.get("new_files", []):
                if filepath not in self.session_manager.repo_summaries:
                    self._newly_created_files.add(filepath)
        
        is_mid_turn_commit = SideEffect.MID_TURN_COMMIT in side_effects or (
            cmd and cmd.tool_name == "commit" and result.get("success")
        )
        if is_mid_turn_commit:
            self.session_manager.mark_mid_turn_commit()
    
    def _finish_stream_processing(self, result: dict[str, Any]) -> None:
        """Finish processing stream result after inline commands."""
        # Record tool calls if present
        if result.get("tool_calls"):
            self.session_manager.append_tool_call(
                result["tool_calls"], result.get("content") or ""
            )
            self._execute_tool_calls(result["tool_calls"])
            return
        
        # Final text response
        if result.get("content"):
            self.session_manager.append_assistant_message(result["content"])
        
        # Generate summaries for new files
        if self._newly_created_files:
            for filepath in self._newly_created_files:
                self.session_manager.generate_summary_for_file(filepath)
            self._newly_created_files.clear()
        
        # Commit the turn
        commit_oid = self.session_manager.commit_ai_turn(self.messages)
        
        self.state = SessionState.IDLE
        self._emit_event(TurnFinishedEvent(commit_oid))
    
    def _execute_tool_calls(self, tool_calls: list[dict[str, Any]]) -> None:
        """Execute tool calls in background thread."""
        from forge.ui.ai_chat_widget import ToolExecutionWorker
        
        self._pending_tools = self.session_manager.tool_manager.discover_tools()
        
        self._tool_thread = QThread()
        self._tool_worker = ToolExecutionWorker(
            tool_calls,
            self.session_manager.tool_manager,
            self.session_manager,
        )
        self._tool_worker.moveToThread(self._tool_thread)
        
        self._tool_worker.tool_started.connect(self._on_tool_started)
        self._tool_worker.tool_finished.connect(self._on_tool_finished)
        self._tool_worker.all_finished.connect(self._on_tools_all_finished)
        self._tool_worker.error.connect(self._on_tool_error)
        self._tool_thread.started.connect(self._tool_worker.run)
        
        self._tool_thread.start()
    
    def _on_tool_started(self, tool_name: str, tool_args: dict[str, Any]) -> None:
        """Handle tool execution starting."""
        self._emit_event(ToolStartedEvent(tool_name, tool_args))
    
    def _on_tool_finished(
        self, tool_call_id: str, tool_name: str, tool_args: dict[str, Any], result: dict[str, Any]
    ) -> None:
        """Handle individual tool completion."""
        import json
        from forge.tools.side_effects import SideEffect
        
        # Add tool result to messages
        result_json = json.dumps(result)
        self.add_message({
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": result_json,
            "_skip_display": True,
        })
        self.session_manager.append_tool_result(tool_call_id, result_json)
        
        # Track side effects
        side_effects = result.get("side_effects", [])
        
        if SideEffect.FILES_MODIFIED in side_effects:
            for filepath in result.get("modified_files", []):
                self._pending_file_updates.append((filepath, tool_call_id))
        
        if SideEffect.NEW_FILES_CREATED in side_effects:
            for filepath in result.get("new_files", []):
                if filepath not in self.session_manager.repo_summaries:
                    self._newly_created_files.add(filepath)
        
        if SideEffect.MID_TURN_COMMIT in side_effects:
            self.session_manager.mark_mid_turn_commit()
        
        # Handle compact tool
        if result.get("compact") and result.get("success"):
            from_id = result.get("from_id", "")
            to_id = result.get("to_id", "")
            summary = result.get("summary", "")
            self.session_manager.compact_tool_results(from_id, to_id, summary)
        
        self._emit_event(ToolFinishedEvent(tool_call_id, tool_name, tool_args, result))
    
    def _on_tools_all_finished(self, results: list[dict[str, Any]]) -> None:
        """Handle all tools completed."""
        if self._tool_thread:
            self._tool_thread.quit()
            self._tool_thread.wait()
            self._tool_thread = None
            self._tool_worker = None
        
        # Track executed IDs
        batch_executed_ids = {r["tool_call"]["id"] for r in results}
        self._turn_executed_tool_ids.update(batch_executed_ids)
        
        # Filter unattempted tool calls
        for msg in reversed(self.messages):
            if msg.get("role") == "assistant" and "tool_calls" in msg:
                msg["tool_calls"] = [
                    tc for tc in msg["tool_calls"]
                    if tc.get("id") in self._turn_executed_tool_ids
                ]
                break
        
        self.session_manager.prompt_manager.filter_tool_calls(self._turn_executed_tool_ids)
        
        # Apply pending file updates
        for filepath, tool_call_id in self._pending_file_updates:
            self.session_manager.file_was_modified(filepath, tool_call_id)
        self._pending_file_updates = []
        
        # Check for queued message
        if self._queued_message:
            queued = self._queued_message
            self._queued_message = None
            self.add_message({"role": "user", "content": queued, "_mid_turn": True})
            self.session_manager.append_user_message(queued)
        
        self._continue_after_tools()
    
    def _on_tool_error(self, error_msg: str) -> None:
        """Handle tool execution error."""
        if self._tool_thread:
            self._tool_thread.quit()
            self._tool_thread.wait()
            self._tool_thread = None
            self._tool_worker = None
        
        self.state = SessionState.ERROR
        self._emit_event(ErrorEvent(f"Tool execution error: {error_msg}"))
    
    def _continue_after_tools(self) -> None:
        """Continue LLM conversation after tool execution."""
        self._process_llm_request()
    
    # === REWIND/TRUNCATE OPERATIONS ===
    
    def rewind_to_message(self, message_index: int) -> bool:
        """Rewind conversation to a specific message index.
        
        Returns True if successful, False if invalid state or index.
        """
        if self._state == SessionState.RUNNING:
            return False
        
        if message_index < 0 or message_index >= len(self.messages):
            return False
        
        # Truncate messages
        self.truncate_messages(message_index + 1)
        
        # Rebuild prompt manager state
        self._rebuild_prompt_manager()
        
        return True
    
    def revert_turn(self, first_message_index: int) -> bool:
        """Revert a turn and all following turns.
        
        Returns True if successful.
        """
        if self._state == SessionState.RUNNING:
            return False
        
        if first_message_index < 1 or first_message_index >= len(self.messages):
            return False
        
        # Truncate to before this turn
        self.truncate_messages(first_message_index)
        self._rebuild_prompt_manager()
        return True
    
    def revert_to_turn(self, first_message_index: int) -> bool:
        """Revert TO a turn (keep this turn, undo later).
        
        Returns True if successful.
        """
        if self._state == SessionState.RUNNING:
            return False
        
        # Find end of this turn
        end_idx = len(self.messages)
        for i in range(first_message_index + 1, len(self.messages)):
            if self.messages[i].get("role") == "user" and not self.messages[i].get("_ui_only"):
                end_idx = i
                break
        
        self.truncate_messages(end_idx)
        self._rebuild_prompt_manager()
        return True
    
    def _rebuild_prompt_manager(self) -> None:
        """Rebuild prompt manager state from current messages."""
        self.session_manager.prompt_manager.clear_conversation()
        
        for msg in self.messages:
            if msg.get("_ui_only"):
                continue
            role = msg.get("role")
            content = msg.get("content", "")
            if role == "user":
                self.session_manager.append_user_message(content)
            elif role == "assistant":
                if "tool_calls" in msg:
                    self.session_manager.append_tool_call(msg["tool_calls"], content)
                elif content:
                    self.session_manager.append_assistant_message(content)
            elif role == "tool":
                tool_call_id = msg.get("tool_call_id", "")
                self.session_manager.append_tool_result(tool_call_id, content)
    
    # --- Child Session Management ---
    
    def spawn_child(self, branch_name: str) -> None:
        """Register a child session branch."""
        if branch_name not in self._child_sessions:
            self._child_sessions.append(branch_name)
    
    def set_parent(self, parent_branch: str) -> None:
        """Set the parent session branch."""
        self._parent_session = parent_branch
    
    def yield_waiting(self, message: str) -> None:
        """
        Yield execution, waiting for children or input.
        
        Called by wait_session tool when no children are ready,
        or by done() when asking a question.
        """
        self._yield_message = message
        self.state = SessionState.WAITING_CHILDREN
        
        # Commit current state
        self.session_manager.commit_ai_turn(self.messages)
    
    def get_session_metadata(self) -> dict[str, Any]:
        """Get metadata for session.json persistence."""
        return {
            "parent_session": self._parent_session,
            "child_sessions": self._child_sessions,
            "state": self._state,
            "yield_message": self._yield_message,
        }