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

    # Compaction nudge thresholds
    TOKEN_THRESHOLD = 30000  # Warn when total tokens exceed this
    TOOL_CALL_THRESHOLD = 15  # Warn when tool calls since last compaction exceed this
    HYSTERESIS_FACTOR = 0.7  # Don't re-warn until below threshold * this factor

    def __init__(self, system_prompt: str) -> None:
        self.blocks: list[ContentBlock] = []
        self.system_prompt = system_prompt

        # Rolling counter for user-friendly tool call IDs
        self._next_tool_id: int = 1
        # Mapping from user-friendly ID -> actual tool_call_id
        self._tool_id_map: dict[str, str] = {}

        # Compaction nudge state
        self._last_compaction_tool_id: int = 0  # Tool ID at last compaction
        self._nudge_suppressed: bool = False  # True = already warned, waiting for hysteresis

        # Add system prompt as first block
        self.blocks.append(
            ContentBlock(
                block_type=BlockType.SYSTEM,
                content=system_prompt,
            )
        )

    def set_summaries(self, summaries: dict[str, str]) -> None:
        """
        Set repository summaries. Can be called multiple times (replaces existing).

        This is a snapshot that won't update mid-session, enabling prompt caching.
        The AI will see actual file content for any files in active context,
        so outdated summaries are not a problem.

        Args:
            summaries: Dict of filepath -> summary text
        """
        if not summaries:
            return

        print(f"📋 PromptManager: Setting summaries for {len(summaries)} files")

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
            lines.append(f"## {filepath}\n{summary}\n")

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

        # Scan backward to find the target file, collecting all file blocks we pass
        # This relocates all files after the target to maintain contiguous file blocks at tail
        files_to_relocate: list[ContentBlock] = []
        target_found = False

        for i in range(len(self.blocks) - 1, -1, -1):
            block = self.blocks[i]
            if block.block_type == BlockType.FILE_CONTENT and not block.deleted:
                files_to_relocate.append(block)
                block.deleted = True

                if block.metadata.get("filepath") == filepath:
                    target_found = True
                    print(f"   ↳ Found target {filepath}, relocating {len(files_to_relocate)} file(s)")
                    break

        # Re-append collected files (excluding target) in original order
        # We collected in reverse order, so reverse to restore original order
        if target_found and len(files_to_relocate) > 1:
            # Pop the target (it was added last during backward scan)
            files_to_relocate.pop()
            # Reverse to get original order, then append
            for block in reversed(files_to_relocate):
                # Re-append with original content (just mark as not deleted and move to end)
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
        self._last_compaction_tool_id = 0
        self._nudge_suppressed = False

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

    def compact_tool_results(self, tool_call_ids: list[str], summary: str) -> tuple[int, list[str]]:
        """
        Replace tool result blocks with a compact summary.

        Only compacts the TOOL_RESULT blocks themselves. FILE_CONTENT blocks
        are NOT removed - they represent the actual file content in context,
        which is the authoritative view of the file.

        Args:
            tool_call_ids: List of tool_call_ids to compact (can be user-friendly
                          IDs like "1", "2" or raw tool_call_ids)
            summary: Summary text to replace the results with
                     Should include enough detail to stay oriented on what changed,
                     e.g., "Added calculate_totals() and format_output() functions to utils.py"

        Returns:
            Tuple of (number of blocks compacted, list of missing IDs)
        """
        compacted = 0

        # Reset compaction nudge state - they just compacted!
        self._last_compaction_tool_id = self._next_tool_id - 1
        self._nudge_suppressed = False

        # Translate user-friendly IDs to actual tool_call_ids
        ids_set = self._resolve_tool_ids(tool_call_ids)

        # Track which user IDs couldn't be resolved or found
        existing_ids = {
            block.metadata.get("tool_call_id")
            for block in self.blocks
            if block.block_type == BlockType.TOOL_RESULT and not block.deleted
        }

        # Find missing user IDs (ones that don't exist or couldn't be resolved)
        missing_user_ids = []
        for user_id in tool_call_ids:
            resolved = self._tool_id_map.get(user_id, user_id)
            if resolved not in existing_ids:
                missing_user_ids.append(user_id)

        print(f"📦 Compact requested for {len(tool_call_ids)} IDs: {tool_call_ids[:3]}...")
        if missing_user_ids:
            print(f"📦 WARNING: {len(missing_user_ids)} IDs not found: {missing_user_ids[:3]}...")

        for block in self.blocks:
            if block.deleted:
                continue

            # Compact tool result blocks
            if (
                block.block_type == BlockType.TOOL_RESULT
                and block.metadata.get("tool_call_id") in ids_set
            ):
                # Replace content with summary (first match gets summary, rest get minimal)
                user_id = block.metadata.get("user_id", "?")
                if compacted == 0:
                    block.content = f"[COMPACTED] {summary}"
                else:
                    block.content = "[COMPACTED - see above]"
                compacted += 1
                print(f"📦 Compacted tool result #{user_id}")

            # Also compact the corresponding tool call blocks
            # These contain the full arguments (e.g., search/replace strings)
            elif block.block_type == BlockType.TOOL_CALL:
                tool_calls = block.metadata.get("tool_calls", [])
                compacted_calls = []
                any_compacted = False
                for tc in tool_calls:
                    tc_id = tc.get("id", "")
                    if tc_id in ids_set:
                        # Replace with minimal stub
                        compacted_calls.append(
                            {
                                "id": tc_id,
                                "type": "function",
                                "function": {
                                    "name": tc.get("function", {}).get("name", "?"),
                                    "arguments": '{"_compacted": true}',
                                },
                            }
                        )
                        any_compacted = True
                    else:
                        compacted_calls.append(tc)
                if any_compacted:
                    block.metadata["tool_calls"] = compacted_calls

        return compacted, missing_user_ids

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

    def _check_compaction_nudge(self) -> bool:
        """
        Check if we should inject a compaction nudge into the conversation.

        Uses hysteresis to avoid repeated warnings:
        - Warn when exceeding threshold
        - Don't re-warn until dropping below threshold * HYSTERESIS_FACTOR

        Returns True if nudge was injected, False otherwise.
        """
        stats = self.get_context_stats()
        total_tokens = stats["total_tokens"]
        tool_calls_since_compaction = (self._next_tool_id - 1) - self._last_compaction_tool_id

        # Check if we're below hysteresis threshold (reset suppression)
        token_hysteresis = self.TOKEN_THRESHOLD * self.HYSTERESIS_FACTOR
        tool_hysteresis = int(self.TOOL_CALL_THRESHOLD * self.HYSTERESIS_FACTOR)

        if total_tokens < token_hysteresis and tool_calls_since_compaction < tool_hysteresis:
            self._nudge_suppressed = False
            return False

        # If already warned and still above threshold, don't re-warn
        if self._nudge_suppressed:
            return False

        # Check if we exceed thresholds
        exceeds_tokens = total_tokens > self.TOKEN_THRESHOLD
        exceeds_tools = tool_calls_since_compaction > self.TOOL_CALL_THRESHOLD

        if not (exceeds_tokens or exceeds_tools):
            return False

        # Build nudge message and inject it as a system message in the conversation
        self._nudge_suppressed = True  # Don't warn again until hysteresis reset

        reasons = []
        if exceeds_tokens:
            reasons.append(f"context is {total_tokens // 1000}k tokens")
        if exceeds_tools:
            reasons.append(f"{tool_calls_since_compaction} tool calls since last compaction")

        nudge_msg = (
            f"<system_nudge>\n"
            f"⚠️ COMPACTION SUGGESTED: {', '.join(reasons)}.\n\n"
            f"Use the `compact` tool to summarize old tool results and reduce context size. "
            f"This improves cache efficiency and reduces costs.\n\n"
            f"Example: compact tool_call_ids=['1','2','3',...] with a summary of what those calls accomplished.\n"
            f"</system_nudge>"
        )

        # Inject as a user message so it appears in the conversation flow
        self.blocks.append(
            ContentBlock(
                block_type=BlockType.USER_MESSAGE,
                content=nudge_msg,
                metadata={"is_system_nudge": True},
            )
        )
        print(f"📢 Injected compaction nudge: {', '.join(reasons)}")
        return True

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

        Skips deleted blocks. Places cache_control on the last content block.

        Groups consecutive user-role blocks (SUMMARIES, FILE_CONTENT, USER_MESSAGE)
        into single messages to avoid consecutive user messages which break the API.

        Injects context stats at the end of the final user message group, giving
        the AI awareness of context size and session cost before responding.

        Also checks if a compaction nudge should be injected into the conversation.
        """
        # Check if we should nudge (this may inject a USER_MESSAGE block)
        self._check_compaction_nudge()

        messages: list[dict[str, Any]] = []

        # Filter out deleted blocks
        active_blocks = [b for b in self.blocks if not b.deleted]

        if not active_blocks:
            return messages

        # Find the index of the last content block (for cache_control placement)
        last_content_idx = -1
        for i, block in enumerate(active_blocks):
            if block.block_type not in (BlockType.TOOL_CALL,):
                last_content_idx = i

        # Find the last user message group (where we'll inject stats)
        last_user_group_start = -1
        user_group_types = (BlockType.SUMMARIES, BlockType.FILE_CONTENT, BlockType.USER_MESSAGE)
        for i, block in enumerate(active_blocks):
            # Check if this block starts a new user group
            is_user_block = block.block_type in user_group_types
            is_new_group = i == 0 or active_blocks[i - 1].block_type not in user_group_types
            if is_user_block and is_new_group:
                last_user_group_start = i

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
                group_start = i
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

                # Inject conversation recap and context stats at the end of the LAST user group
                # This gives AI orientation and current stats right before it responds
                if group_start == last_user_group_start:
                    # Recap helps orient when file contents push conversation far back
                    recap_block = self.format_conversation_recap()
                    content_blocks.append({"type": "text", "text": recap_block})

                    stats_block = self.format_context_stats_block()
                    # Stats go after cache_control block, so not cached (always fresh)
                    content_blocks.append({"type": "text", "text": stats_block})

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
        """Create assistant message with tool_calls and optional content"""
        tool_calls = block.metadata.get("tool_calls", [])
        msg: dict[str, Any] = {
            "role": "assistant",
            "tool_calls": tool_calls,
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
