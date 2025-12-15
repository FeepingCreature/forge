"""
PromptManager - Manages prompt construction with cache optimization

The prompt is treated as an append-only stream with occasional deletions.
When a file is modified, its old content block is deleted and new content
is appended at the end. This maximizes cache reuse since Anthropic caches
per-block with prefix matching.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


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

    def __init__(self, system_prompt: str) -> None:
        self.blocks: list[ContentBlock] = []
        self.system_prompt = system_prompt

        # Add system prompt as first block
        self.blocks.append(
            ContentBlock(
                block_type=BlockType.SYSTEM,
                content=system_prompt,
            )
        )

    def set_summaries(self, summaries: dict[str, str]) -> None:
        """
        Set repository summaries. Should only be called once at session start.

        This is a snapshot that won't update mid-session, enabling prompt caching.
        The AI will see actual file content for any files in active context,
        so outdated summaries are not a problem.

        Args:
            summaries: Dict of filepath -> summary text
        """
        if not summaries:
            return

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

        Args:
            filepath: Path to the file
            content: Full file content
            note: Optional note (e.g., "summary may be outdated")
            tool_call_id: If this file was just modified by a tool, the tool call ID
        """
        # Delete old version if exists (linear scan is fine for ~200 files max)
        for block in self.blocks:
            if (
                block.block_type == BlockType.FILE_CONTENT
                and block.metadata.get("filepath") == filepath
                and not block.deleted
            ):
                block.deleted = True
                break

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
        for block in self.blocks:
            if (
                block.block_type == BlockType.FILE_CONTENT
                and block.metadata.get("filepath") == filepath
                and not block.deleted
            ):
                block.deleted = True
                break

    def append_user_message(self, content: str) -> None:
        """Add a user message to the stream"""
        self.blocks.append(
            ContentBlock(
                block_type=BlockType.USER_MESSAGE,
                content=content,
            )
        )

    def append_assistant_message(self, content: str) -> None:
        """Add an assistant message to the stream"""
        self.blocks.append(
            ContentBlock(
                block_type=BlockType.ASSISTANT_MESSAGE,
                content=content,
            )
        )

    def append_tool_call(self, tool_calls: list[dict[str, Any]], content: str = "") -> None:
        """Add tool calls to the stream, optionally with accompanying text content"""
        self.blocks.append(
            ContentBlock(
                block_type=BlockType.TOOL_CALL,
                content=content,  # Assistant's text that accompanied the tool calls
                metadata={"tool_calls": tool_calls},
            )
        )

    def append_tool_result(self, tool_call_id: str, result: str) -> None:
        """Add a tool result to the stream"""
        self.blocks.append(
            ContentBlock(
                block_type=BlockType.TOOL_RESULT,
                content=result,
                metadata={"tool_call_id": tool_call_id},
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

    def to_messages(self) -> list[dict[str, Any]]:
        """
        Convert the block stream to API message format.

        Skips deleted blocks. Places cache_control on the last content block.

        Groups consecutive user-role blocks (SUMMARIES, FILE_CONTENT, USER_MESSAGE)
        into single messages to avoid consecutive user messages which break the API.
        """
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
        return {
            "role": "tool",
            "tool_call_id": block.metadata.get("tool_call_id", ""),
            "content": [self._make_content_block(block.content, is_last)],
        }
