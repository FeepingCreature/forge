"""
PromptManager - Manages prompt construction with cache optimization

The prompt is treated as an append-only stream with occasional deletions.
When a file is modified, its old content block is deleted and new content
is appended at the end. This maximizes cache reuse since Anthropic caches
per-block with prefix matching.
"""

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from forge.llm.cost_tracker import COST_TRACKER
from forge.prompts.system import get_system_prompt


class BlockType(Enum):
    SYSTEM = "system"
    SUMMARIES = "summaries"
    FILE_CONTENT = "file_content"
    USER_MESSAGE = "user_message"
    ASSISTANT_MESSAGE = "assistant_message"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"


@dataclass
class ContentBlock:
    """A block in the prompt stream"""

    block_type: BlockType
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)
    deleted: bool = False


class PromptManager:
    """
    Manages prompt as an append-only stream with deletions.

    Key operations:
    - append_*: Add content to the stream
    - file_modified: Delete old file content, append new at end
    - to_messages: Convert to API format with cache_control on last block
    """

    def __init__(self, system_prompt: str | None = None, edit_format: str = "xml") -> None:
        self.blocks: list[ContentBlock] = []
        self.edit_format = edit_format

        # Generate system prompt based on edit format if not provided
        if system_prompt is None:
            system_prompt = get_system_prompt(edit_format)
        self.system_prompt = system_prompt

        # Rolling counter for user-friendly tool call IDs
        self._next_tool_id: int = 1
        # Mapping from user-friendly ID -> actual tool_call_id
        self._tool_id_map: dict[str, str] = {}

        # Add system prompt as first block
        self.blocks.append(
            ContentBlock(
                block_type=BlockType.SYSTEM,
                content=system_prompt,
            )
        )

    def _format_file_size(self, size_bytes: int) -> str:
        """Format file size in human-readable form"""
        if size_bytes < 1024:
            return f"{size_bytes} B"
        elif size_bytes < 1024 * 1024:
            return f"{size_bytes / 1024:.1f} KB"
        else:
            return f"{size_bytes / (1024 * 1024):.1f} MB"

    def set_summaries(
        self,
        summaries: dict[str, str],
        file_sizes: dict[str, int] | None = None,
        files_beyond_budget: list[str] | None = None,
    ) -> None:
        """
        Set repository summaries. Can be called multiple times (replaces existing).

        This is a snapshot that won't update mid-session, enabling prompt caching.
        The AI will see actual file content for any files in active context,
        so outdated summaries are not a problem.

        Args:
            summaries: Dict of filepath -> summary text
            file_sizes: Optional dict of filepath -> size in bytes
            files_beyond_budget: List of files that exceeded token budget (no summaries)
        """
        if not summaries and not files_beyond_budget:
            return

        total_files = len(summaries) + len(files_beyond_budget or [])
        print(
            f"📋 PromptManager: Setting summaries for {len(summaries)} files ({total_files} total)"
        )

        # Delete any existing summaries block first (avoid duplication)
        for block in self.blocks:
            if block.block_type == BlockType.SUMMARIES and not block.deleted:
                block.deleted = True
                print("   ↳ Deleted old summaries block")
                break

        # Format summaries with note about being a snapshot
        lines = [
            "# Repository File Summaries (snapshot from session start)\n\n",
            "*These summaries were generated when your session started and won't update. ",
            "When you work with a file, you'll see its actual current content below.*\n\n",
        ]
        for filepath, summary in sorted(summaries.items()):
            # Include file size if available
            if file_sizes and filepath in file_sizes:
                size_str = self._format_file_size(file_sizes[filepath])
                lines.append(f"## {filepath} ({size_str})\n{summary}\n")
            else:
                lines.append(f"## {filepath}\n{summary}\n")

        # Add files beyond budget as a simple list
        if files_beyond_budget:
            lines.append("\n# Additional Files (use scout to investigate)\n\n")
            lines.append(
                "*These files exceeded the summary token budget. "
                "Use the `scout` tool with a question to examine them.*\n\n"
            )
            for filepath in files_beyond_budget:
                if file_sizes and filepath in file_sizes:
                    size_str = self._format_file_size(file_sizes[filepath])
                    lines.append(f"- {filepath} ({size_str})\n")
                else:
                    lines.append(f"- {filepath}\n")

        self.blocks.append(
            ContentBlock(
                block_type=BlockType.SUMMARIES,
                content="".join(lines),
            )
        )

    def append_file_content(
        self, filepath: str, content: str, note: str = "", tool_call_id: str | None = None
    ) -> None:
        """
        Add file content to the stream, removing any previous version.

        Cache optimization: When updating a file, we relocate ALL file blocks
        that appear after it to the end of the stream. This ensures all file
        blocks remain contiguous at the tail, so future file edits only
        invalidate from the file block position forward (not the entire context).

        Args:
            filepath: Path to the file
            content: Full file content
            note: Optional note (e.g., "summary may be outdated")
            tool_call_id: If this file was just modified by a tool, the tool call ID
        """
        print(f"📄 PromptManager: Appending file content for {filepath} ({len(content)} chars)")

        # Cache optimization: relocate all file blocks from the earliest target instance forward.
        #
        # Example: S Z A B C x x A x x x U -> we want S Z x x x x x B C A U
        #
        # Rationale: When we delete all A blocks, cache invalidates from the earliest A forward.
        # Since B and C are already losing their cache position, we relocate them too.
        # This keeps all file blocks contiguous at the tail, so future edits only invalidate
        # from the file block region forward. A goes last to maintain LRU ordering.
        #
        # Algorithm: two passes.
        # Pass 1: find the earliest (first in message order) target block index
        # Pass 2: collect and delete all file blocks from that index forward
        earliest_target_idx: int | None = next(
            (
                i
                for i, block in enumerate(self.blocks)
                if block.block_type == BlockType.FILE_CONTENT
                and not block.deleted
                and block.metadata.get("filepath") == filepath
            ),
            None,
        )

        if earliest_target_idx is None:
            # New file, no existing blocks to relocate
            print("   ↳ New file, no existing blocks")
        else:
            # Collect all file blocks from earliest_target_idx to end
            files_to_relocate: list[ContentBlock] = []
            for i in range(earliest_target_idx, len(self.blocks)):
                block = self.blocks[i]
                if block.block_type == BlockType.FILE_CONTENT and not block.deleted:
                    files_to_relocate.append(block)
                    block.deleted = True

            print(f"   ↳ Relocating {len(files_to_relocate)} file(s) for {filepath} update")

            # Re-append non-target files in original order (already in order from forward scan)
            for block in files_to_relocate:
                if block.metadata.get("filepath") != filepath:
                    self.blocks.append(
                        ContentBlock(
                            block_type=BlockType.FILE_CONTENT,
                            content=block.content,
                            metadata=block.metadata.copy(),
                        )
                    )
                    print(f"   ↳ Relocated {block.metadata.get('filepath')}")

        # Format content block with explicit annotation
        # Make it VERY clear this is informative context, not a question
        if tool_call_id:
            header = (
                f"[CONTEXT: File contents for {filepath} after tool call {tool_call_id}. "
                f"This is purely informative - showing the result of the tool operation.]"
            )
        elif note:
            header = (
                f"[CONTEXT: File contents for {filepath}. "
                f"This is purely informative context, not a question. NOTE: {note}]"
            )
        else:
            header = (
                f"[CONTEXT: File contents for {filepath}. "
                f"This is purely informative context, not a question.]"
            )

        text = f"{header}\n\n```\n{content}\n```"

        self.blocks.append(
            ContentBlock(
                block_type=BlockType.FILE_CONTENT,
                content=text,
                metadata={"filepath": filepath, "tool_call_id": tool_call_id},
            )
        )

    def remove_file_content(self, filepath: str) -> None:
        """
        Remove a file's content from the stream.

        Note: Caller should ensure summary is updated before calling this,
        since the summary will be the only hint about this file.
        """
        print(f"🗑️  PromptManager: Removing file content for {filepath}")
        for block in self.blocks:
            if (
                block.block_type == BlockType.FILE_CONTENT
                and block.metadata.get("filepath") == filepath
                and not block.deleted
            ):
                block.deleted = True
                print(f"   ↳ Found and deleted {filepath}")
                break

    def append_user_message(self, content: str) -> None:
        """Add a user message to the stream"""
        print(f"👤 PromptManager: Appending user message ({len(content)} chars)")
        self.blocks.append(
            ContentBlock(
                block_type=BlockType.USER_MESSAGE,
                content=content,
            )
        )

    def append_assistant_message(self, content: str) -> None:
        """Add an assistant message to the stream"""
        print(f"🤖 PromptManager: Appending assistant message ({len(content)} chars)")
        self.blocks.append(
            ContentBlock(
                block_type=BlockType.ASSISTANT_MESSAGE,
                content=content,
            )
        )

    def append_tool_call(self, tool_calls: list[dict[str, Any]], content: str = "") -> None:
        """Add tool calls to the stream, optionally with accompanying text content"""
        tool_names = [tc.get("function", {}).get("name", "?") for tc in tool_calls]
        print(f"🔧 PromptManager: Appending tool calls: {tool_names}")
        self.blocks.append(
            ContentBlock(
                block_type=BlockType.TOOL_CALL,
                content=content,  # Assistant's text that accompanied the tool calls
                metadata={"tool_calls": tool_calls},
            )
        )

    def filter_tool_calls(self, executed_tool_ids: set[str]) -> None:
        """
        Filter tool calls to only include those that were actually executed.

        When tools are chained (A → B → C) and B fails, C is never attempted.
        The API requires every tool_call to have a corresponding tool result,
        so we must remove C from the assistant message's tool_calls list.

        This is called after tool execution completes, before the next API request.

        Args:
            executed_tool_ids: Set of tool_call IDs that were actually executed
        """
        for block in reversed(self.blocks):
            if block.deleted or block.block_type != BlockType.TOOL_CALL:
                continue

            tool_calls = block.metadata.get("tool_calls", [])
            original_count = len(tool_calls)

            # Filter to only executed tool calls
            filtered = [tc for tc in tool_calls if tc.get("id") in executed_tool_ids]

            if len(filtered) < original_count:
                block.metadata["tool_calls"] = filtered
                dropped = original_count - len(filtered)
                print(f"📦 PromptManager: Filtered out {dropped} unattempted tool call(s)")

            # Only filter the most recent TOOL_CALL block (the one that just executed)
            break

    def append_tool_result(self, tool_call_id: str, result: str) -> None:
        """Add a tool result to the stream"""
        # Validate tool_call_id - Anthropic requires pattern ^[a-zA-Z0-9_-]+$
        if not tool_call_id:
            print(f"❌ ERROR: Empty tool_call_id! Result: {result[:100]}...")
            raise ValueError(f"tool_call_id cannot be empty (result: {result[:100]}...)")

        # Assign a user-friendly integer ID
        user_id = str(self._next_tool_id)
        self._next_tool_id += 1
        self._tool_id_map[user_id] = tool_call_id

        print(
            f"📋 PromptManager: Appending tool result #{user_id} for {tool_call_id} ({len(result)} chars)"
        )
        self.blocks.append(
            ContentBlock(
                block_type=BlockType.TOOL_RESULT,
                content=result,
                metadata={"tool_call_id": tool_call_id, "user_id": user_id},
            )
        )

    def get_active_files(self) -> list[str]:
        """Get list of files currently in context (not deleted)"""
        files = []
        for block in self.blocks:
            if (
                block.block_type == BlockType.FILE_CONTENT
                and not block.deleted
                and "filepath" in block.metadata
            ):
                files.append(block.metadata["filepath"])
        return files

    def clear_conversation(self) -> None:
        """
        Clear all conversation blocks, keeping system prompt, summaries, and file content.

        Used when rewinding conversation to rebuild from a subset of messages.
        """
        print("🔄 PromptManager: Clearing conversation blocks")

        # Keep only non-conversation blocks
        keep_types = {BlockType.SYSTEM, BlockType.SUMMARIES, BlockType.FILE_CONTENT}
        self.blocks = [b for b in self.blocks if b.block_type in keep_types]

        # Reset tool ID tracking
        self._next_tool_id = 1
        self._tool_id_map = {}

    def _resolve_tool_ids(self, ids: list[str]) -> set[str]:
        """
        Resolve user-friendly IDs (like "1", "2") to actual tool_call_ids.

        Accepts both user IDs and raw tool_call_ids for flexibility.
        """
        resolved = set()
        for id_str in ids:
            # Check if it's a user-friendly ID we can translate
            if id_str in self._tool_id_map:
                resolved.add(self._tool_id_map[id_str])
            else:
                # Assume it's already a raw tool_call_id
                resolved.add(id_str)
        return resolved

    def compact_think_call(self, tool_call_id: str) -> bool:
        """
        Compact a think tool call by removing the scratchpad from its arguments.

        The think tool's value is in generating the scratchpad (extended reasoning),
        but we don't need to keep it in context - only the conclusion matters,
        and that's in the tool result.

        Args:
            tool_call_id: The ID of the think tool call to compact

        Returns:
            True if the tool call was found and compacted, False otherwise
        """
        for block in self.blocks:
            if block.deleted or block.block_type != BlockType.TOOL_CALL:
                continue

            tool_calls = block.metadata.get("tool_calls", [])
            for i, tc in enumerate(tool_calls):
                if tc.get("id") == tool_call_id:
                    func = tc.get("function", {})
                    if func.get("name") == "think":
                        # Replace arguments with minimal stub (keep conclusion reference)
                        tool_calls[i] = {
                            "id": tool_call_id,
                            "type": "function",
                            "function": {
                                "name": "think",
                                "arguments": '{"_compacted": true}',
                            },
                        }
                        print(f"🧠 Compacted think tool call {tool_call_id}")
                        return True
        return False

    def compact_tool_results(
        self, from_id: str, to_id: str, summary: str
    ) -> tuple[int, str | None]:
        """
        Replace tool result blocks in a range with a compact summary.

        Compacts all tool results and assistant messages between from_id and to_id
        (inclusive). FILE_CONTENT blocks are NOT removed.

        Args:
            from_id: First tool_call_id to compact (user-friendly like "1" or raw)
            to_id: Last tool_call_id to compact (user-friendly like "1" or raw)
            summary: Summary text to replace the results with

        Returns:
            Tuple of (number of blocks compacted, error message or None)
        """
        # Resolve user-friendly IDs to indices
        from_idx: int | None = None
        to_idx: int | None = None

        # Convert user IDs to ints for range comparison
        try:
            from_int = int(from_id)
            to_int = int(to_id)
        except ValueError:
            return 0, f"Invalid IDs: from_id={from_id}, to_id={to_id} (must be integers)"

        if from_int > to_int:
            return 0, f"from_id ({from_int}) must be <= to_id ({to_int})"

        # Find the block indices for the range
        for i, block in enumerate(self.blocks):
            if block.deleted or block.block_type != BlockType.TOOL_RESULT:
                continue
            user_id = block.metadata.get("user_id", "")
            try:
                block_int = int(user_id)
            except (ValueError, TypeError):
                continue
            if block_int == from_int:
                from_idx = i
            if block_int == to_int:
                to_idx = i

        if from_idx is None:
            return 0, f"from_id {from_id} not found"
        if to_idx is None:
            return 0, f"to_id {to_id} not found"

        print(f"📦 Compacting range #{from_id} to #{to_id}")

        # Compact everything in the range
        compacted = 0
        first_result = True

        for i in range(from_idx, to_idx + 1):
            block = self.blocks[i]
            if block.deleted:
                continue

            if block.block_type == BlockType.TOOL_RESULT:
                user_id = block.metadata.get("user_id", "?")
                if first_result:
                    block.content = f"[COMPACTED] {summary}"
                    first_result = False
                else:
                    block.content = "[COMPACTED - see above]"
                compacted += 1
                print(f"📦 Compacted tool result #{user_id}")

            elif block.block_type == BlockType.TOOL_CALL:
                # Compact all tool calls in this block
                tool_calls = block.metadata.get("tool_calls", [])
                compacted_calls = []
                for tc in tool_calls:
                    compacted_calls.append(
                        {
                            "id": tc.get("id", ""),
                            "type": "function",
                            "function": {
                                "name": tc.get("function", {}).get("name", "?"),
                                "arguments": '{"_compacted": true}',
                            },
                        }
                    )
                block.metadata["tool_calls"] = compacted_calls
                # Also truncate the content (contains edit blocks in XML format)
                if block.content and len(block.content) > 100:
                    block.content = block.content[:100] + "..."
                    print("📦 Truncated tool call content in range")

            elif block.block_type == BlockType.ASSISTANT_MESSAGE:
                # Truncate assistant messages in the range
                if len(block.content) > 100:
                    block.content = block.content[:100] + "..."
                    print("📦 Truncated assistant message in range")

        return compacted, None

    def get_last_user_message(self) -> str | None:
        """Get the last user message from the conversation (for commit message context)"""
        for block in reversed(self.blocks):
            if block.block_type == BlockType.USER_MESSAGE and not block.deleted:
                return block.content
        return None

    def estimate_conversation_tokens(self) -> int:
        """
        Estimate tokens used by conversation history (excluding file content).

        File content is counted separately, so this only counts:
        - User messages
        - Assistant messages
        - Tool calls (as JSON)
        - Tool results
        """
        total = 0
        for block in self.blocks:
            if block.deleted:
                continue
            # Skip file content and summaries - those are counted separately
            if block.block_type in (BlockType.FILE_CONTENT, BlockType.SUMMARIES, BlockType.SYSTEM):
                continue
            # Estimate ~3 chars per token (more accurate for code)
            total += len(block.content) // 3
            # For tool calls, also count the tool call metadata
            if block.block_type == BlockType.TOOL_CALL:
                tool_calls = block.metadata.get("tool_calls", [])
                for tc in tool_calls:
                    total += len(json.dumps(tc)) // 3
        return total

    def get_context_stats(self) -> dict[str, Any]:
        """
        Get detailed statistics about context usage.

        Returns dict with:
        - total_tokens: Estimated total tokens in context
        - system_tokens: System prompt tokens
        - summaries_tokens: Repository summaries tokens
        - files_tokens: Active file content tokens
        - conversation_tokens: Conversation history tokens
        - session_cost: Total session cost so far (USD)
        - file_count: Number of active files
        """
        stats: dict[str, Any] = {
            "system_tokens": 0,
            "summaries_tokens": 0,
            "files_tokens": 0,
            "conversation_tokens": 0,
            "file_count": 0,
            "session_cost": COST_TRACKER.total_cost,
            "daily_cost": COST_TRACKER.daily_cost,
        }

        for block in self.blocks:
            if block.deleted:
                continue

            # Estimate ~3 chars per token (more accurate for code)
            tokens = len(block.content) // 3

            if block.block_type == BlockType.SYSTEM:
                stats["system_tokens"] += tokens
            elif block.block_type == BlockType.SUMMARIES:
                stats["summaries_tokens"] += tokens
            elif block.block_type == BlockType.FILE_CONTENT:
                stats["files_tokens"] += tokens
                stats["file_count"] += 1
            elif block.block_type == BlockType.TOOL_CALL:
                stats["conversation_tokens"] += tokens
                # Also count tool call metadata
                tool_calls = block.metadata.get("tool_calls", [])
                for tc in tool_calls:
                    stats["conversation_tokens"] += len(json.dumps(tc)) // 3
            else:
                stats["conversation_tokens"] += tokens

        stats["total_tokens"] = (
            stats["system_tokens"]
            + stats["summaries_tokens"]
            + stats["files_tokens"]
            + stats["conversation_tokens"]
        )

        return stats

    def _get_context_size_label(self, total_tokens: int) -> str:
        """
        Get a human-readable label for context size.

        Always shown in stats block to give AI awareness of context state.
        """
        if total_tokens < 20000:
            return "small"
        elif total_tokens < 35000:
            return "moderate"
        elif total_tokens < 50000:
            return "large"
        elif total_tokens < 80000:
            return "very large"
        else:
            return "extremely large - compaction strongly recommended"

    def _summarize_tool_call(self, tc: dict[str, Any]) -> str:
        """
        Generate a brief summary of a tool call for the conversation recap.

        Returns something like: "search_replace(filepath='foo.py', ...)"
        """
        func = tc.get("function", {})
        name = func.get("name", "?")
        args_str = func.get("arguments", "{}")

        # Parse arguments to get key info
        try:
            args = json.loads(args_str)
        except json.JSONDecodeError:
            return f"{name}(...)"

        # Handle compacted tool calls
        if args.get("_compacted"):
            return f"{name}([compacted])"

        # Build a brief summary based on tool type
        if name in ("search_replace", "write_file"):
            filepath = args.get("filepath", "?")
            return f"{name}({filepath})"
        elif name == "update_context":
            add = args.get("add", [])
            remove = args.get("remove", [])
            parts = []
            if add:
                parts.append(f"+{len(add)} files")
            if remove:
                parts.append(f"-{len(remove)} files")
            return f"{name}({', '.join(parts) or 'no changes'})"
        elif name == "grep_open" or name == "grep_context":
            pattern = args.get("pattern", "?")
            # Truncate long patterns
            if len(pattern) > 30:
                pattern = pattern[:27] + "..."
            return f"{name}('{pattern}')"
        elif name == "think":
            return "think(...)"
        elif name == "compact":
            ids = args.get("tool_call_ids", [])
            return f"{name}({len(ids)} tool results)"
        elif name == "commit":
            msg = args.get("message", "")
            if len(msg) > 40:
                msg = msg[:37] + "..."
            return f"{name}('{msg}')"
        else:
            # Generic: show first arg key/value if available
            if args:
                first_key = next(iter(args))
                first_val = args[first_key]
                if isinstance(first_val, str) and len(first_val) > 30:
                    first_val = first_val[:27] + "..."
                return f"{name}({first_key}={first_val!r})"
            return f"{name}()"

    def format_conversation_recap(self, max_messages: int = 20) -> str:
        """
        Format a brief recap of the conversation for injection at the end.

        This helps the model maintain orientation when file contents push
        the actual conversation far back in context. Shows:
        - Full user messages (they're short and important)
        - Condensed tool call summaries with IDs
        - Brief assistant text (truncated if long)

        Capped to last `max_messages` messages OR from the last user message,
        whichever includes more. This ensures the current turn is always complete.
        """
        # Collect conversation blocks (skip system, summaries, file content)
        conv_types = {
            BlockType.USER_MESSAGE,
            BlockType.ASSISTANT_MESSAGE,
            BlockType.TOOL_CALL,
            BlockType.TOOL_RESULT,
        }
        conv_blocks = [b for b in self.blocks if not b.deleted and b.block_type in conv_types]

        # Find the last user message index
        last_user_idx = -1
        for i, block in enumerate(conv_blocks):
            if block.block_type == BlockType.USER_MESSAGE and not block.metadata.get(
                "is_system_nudge"
            ):
                last_user_idx = i

        # Calculate start index: either last N messages or from last user message
        start_from_limit = max(0, len(conv_blocks) - max_messages)
        start_from_user = last_user_idx if last_user_idx >= 0 else 0
        start_idx = min(start_from_limit, start_from_user)

        # Slice to the blocks we'll show
        blocks_to_show = conv_blocks[start_idx:]

        # Add indicator if we truncated
        lines = ["## Conversation Recap\n"]
        if start_idx > 0:
            lines.append(f"*[{start_idx} earlier messages omitted]*\n")

        for block in blocks_to_show:
            if block.block_type == BlockType.USER_MESSAGE:
                # Skip system nudges in recap
                if block.metadata.get("is_system_nudge"):
                    continue
                # Show full user message (they're short)
                content = block.content.strip()
                lines.append(f"**User**: {content}\n")

            elif block.block_type == BlockType.ASSISTANT_MESSAGE:
                # Truncate long assistant messages
                content = block.content.strip()
                if len(content) > 200:
                    content = content[:197] + "..."
                lines.append(f"**Assistant**: {content}\n")

            elif block.block_type == BlockType.TOOL_CALL:
                tool_calls = block.metadata.get("tool_calls", [])
                if tool_calls:
                    summaries = [self._summarize_tool_call(tc) for tc in tool_calls]
                    # Show accompanying text if present
                    if block.content.strip():
                        text = block.content.strip()
                        if len(text) > 100:
                            text = text[:97] + "..."
                        lines.append(f"**Assistant**: {text}\n")
                    lines.append(f"  → Tool calls: {', '.join(summaries)}\n")

            elif block.block_type == BlockType.TOOL_RESULT:
                user_id = block.metadata.get("user_id", "?")
                content = block.content
                # Just show success/failure, not the full content
                if content.startswith("[COMPACTED]"):
                    lines.append(f"  ← Result #{user_id}: [compacted]\n")
                elif '"success": false' in content or '"error"' in content:
                    lines.append(f"  ← Result #{user_id}: ✗ (error)\n")
                else:
                    lines.append(f"  ← Result #{user_id}: ✓\n")

        return "".join(lines)

    def format_context_stats_block(self) -> str:
        """
        Format context stats as a compact XML block for injection into the prompt.

        This gives the AI awareness of context size and session cost.
        Includes a persistent context size label (small/moderate/large/etc).
        """
        stats = self.get_context_stats()

        # Also measure the recap size
        recap = self.format_conversation_recap()
        recap_tokens = len(recap) // 3

        # Format session cost
        session_cost = stats["session_cost"]
        daily_cost = stats["daily_cost"]

        def format_cost(cost: float) -> str:
            if cost < 0.01:
                return f"${cost:.4f}"
            else:
                return f"${cost:.2f}"

        cost_str = format_cost(session_cost)
        if daily_cost > session_cost:
            cost_str += f" ({format_cost(daily_cost)} today)"

        # Format token counts with 1 decimal place
        def format_k(tokens: int) -> str:
            return f"{tokens / 1000:.1f}k"

        total_tokens = stats["total_tokens"]
        total_k = format_k(total_tokens)

        # Get context size label (always shown)
        size_label = self._get_context_size_label(total_tokens)

        return (
            f"<context_stats>\n"
            f"  <total_tokens>{total_k} ({size_label})</total_tokens>\n"
            f"  <breakdown>"
            f"system {format_k(stats['system_tokens'])}, "
            f"summaries {format_k(stats['summaries_tokens'])}, "
            f"files {format_k(stats['files_tokens'])} ({stats['file_count']} files), "
            f"conversation {format_k(stats['conversation_tokens'])}"
            f"</breakdown>\n"
            f"  <recap_tokens>{format_k(recap_tokens)}</recap_tokens>\n"
            f"  <session_cost>{cost_str}</session_cost>\n"
            f"</context_stats>"
        )

    def to_messages(self) -> list[dict[str, Any]]:
        """
        Convert the block stream to API message format.

        Skips deleted blocks. Places cache_control on the last content block
        BEFORE the stats/recap injection.

        Groups consecutive user-role blocks (SUMMARIES, FILE_CONTENT, USER_MESSAGE)
        into single messages to avoid consecutive user messages which break the API.

        Injects context stats as a FINAL user message at the very end, ensuring
        they don't cache-invalidate any conversation content that comes before them.
        """
        messages: list[dict[str, Any]] = []

        # Filter out deleted blocks
        active_blocks = [b for b in self.blocks if not b.deleted]

        if not active_blocks:
            return messages

        # Find the index of the last content block (for cache_control placement)
        # This is the last block before stats injection - everything up to here is cacheable
        last_content_idx = -1
        for i, block in enumerate(active_blocks):
            if block.block_type not in (BlockType.TOOL_CALL,):
                last_content_idx = i

        i = 0
        while i < len(active_blocks):
            block = active_blocks[i]
            is_last_content = i == last_content_idx

            if block.block_type == BlockType.SYSTEM:
                messages.append(self._make_system_message(block, is_last_content))
                i += 1

            elif block.block_type in (
                BlockType.SUMMARIES,
                BlockType.FILE_CONTENT,
                BlockType.USER_MESSAGE,
            ):
                # Group ALL consecutive user-role content into a single message
                # This avoids consecutive user messages which break the Anthropic API
                # FILE_CONTENT blocks are annotated explicitly to clarify they're context
                content_blocks = []
                while i < len(active_blocks) and active_blocks[i].block_type in (
                    BlockType.SUMMARIES,
                    BlockType.FILE_CONTENT,
                    BlockType.USER_MESSAGE,
                ):
                    is_this_last = i == last_content_idx
                    content_blocks.append(
                        self._make_content_block(active_blocks[i].content, is_this_last)
                    )
                    i += 1

                messages.append({"role": "user", "content": content_blocks})

            elif block.block_type == BlockType.ASSISTANT_MESSAGE:
                messages.append(self._make_assistant_message(block, is_last_content))
                i += 1

            elif block.block_type == BlockType.TOOL_CALL:
                messages.append(self._make_assistant_tool_call(block, False))
                i += 1

            elif block.block_type == BlockType.TOOL_RESULT:
                messages.append(self._make_tool_result(block, is_last_content))
                i += 1

            else:
                i += 1

        # Inject conversation recap and context stats as a FINAL user message
        # This ensures they're always at the very end, right before the AI responds,
        # and don't cache-invalidate any prior content (since they change every turn).
        #
        # We need to handle the case where the last message is already a user message
        # (can't have two consecutive user messages for Anthropic API).
        recap_block = self.format_conversation_recap()
        stats_block = self.format_context_stats_block()
        stats_content = [
            {"type": "text", "text": recap_block},
            {"type": "text", "text": stats_block},
        ]

        if messages and messages[-1].get("role") == "user":
            # Append to existing user message
            messages[-1]["content"].extend(stats_content)
        else:
            # Add as new user message
            messages.append({"role": "user", "content": stats_content})

        return messages

    def _make_content_block(self, text: str, is_last: bool) -> dict[str, Any]:
        """Create a content block, adding cache_control if it's the last one"""
        block: dict[str, Any] = {"type": "text", "text": text}
        if is_last:
            block["cache_control"] = {"type": "ephemeral"}
        return block

    def _make_system_message(self, block: ContentBlock, is_last: bool) -> dict[str, Any]:
        return {
            "role": "system",
            "content": [self._make_content_block(block.content, is_last)],
        }

    def _make_user_message(self, block: ContentBlock, is_last: bool) -> dict[str, Any]:
        return {
            "role": "user",
            "content": [self._make_content_block(block.content, is_last)],
        }

    def _make_assistant_message(self, block: ContentBlock, is_last: bool) -> dict[str, Any]:
        return {
            "role": "assistant",
            "content": [self._make_content_block(block.content, is_last)],
        }

    def _make_assistant_tool_call(self, block: ContentBlock, is_last: bool) -> dict[str, Any]:
        """Create assistant message with tool_calls and optional content.

        Note: Think tool calls are compacted on-the-fly here - the scratchpad
        is stripped from the arguments so it doesn't bloat API requests.
        The original scratchpad is preserved in the block for session storage/UI.
        """
        tool_calls = block.metadata.get("tool_calls", [])

        # Compact think tool calls on-the-fly (don't mutate original)
        compacted_tool_calls = []
        for tc in tool_calls:
            func = tc.get("function", {})
            if func.get("name") == "think":
                # Replace with compacted version (scratchpad stripped)
                compacted_tool_calls.append(
                    {
                        "id": tc.get("id", ""),
                        "type": "function",
                        "function": {
                            "name": "think",
                            "arguments": '{"_compacted": true}',
                        },
                    }
                )
            else:
                compacted_tool_calls.append(tc)

        msg: dict[str, Any] = {
            "role": "assistant",
            "tool_calls": compacted_tool_calls,
        }
        # Include content if present (assistant may explain what it's doing)
        if block.content:
            msg["content"] = block.content
        return msg

    def _make_tool_result(self, block: ContentBlock, is_last: bool) -> dict[str, Any]:
        """Create tool result message, with cache_control if it's the last content block"""
        tool_call_id = block.metadata.get("tool_call_id", "")
        if not tool_call_id:
            # This should never happen - append_tool_result validates
            raise ValueError("tool_call_id missing from tool result block metadata")

        # Prepend user-friendly ID to content so the LLM can reference it for compacting
        user_id = block.metadata.get("user_id", "?")
        content_with_id = f"[tool_call_id: {user_id}]\n{block.content}"

        return {
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": [self._make_content_block(content_with_id, is_last)],
        }
