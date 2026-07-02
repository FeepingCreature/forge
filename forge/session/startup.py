"""
Session startup helpers - reusable functions for starting/resuming sessions.

This module provides functions that can be used by:
- LiveSession._start_child_session() when spawning children
- Application startup when recovering in-progress sessions
- UI when user clicks on a session in the dropdown
"""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from forge.config.settings import Settings
    from forge.git_backend.repository import ForgeRepository
    from forge.session.live_session import LiveSession
    from forge.session.manager import SessionManager


def replay_messages_to_prompt_manager(
    messages: list[dict[str, Any]],
    session_manager: "SessionManager",
) -> None:
    """
    Replay messages into the prompt manager so the LLM sees them.

    LiveSession.messages is the uncompacted source of truth (for UI display and
    persistence). PromptManager builds the actual LLM request. When loading a
    session from disk, we replay messages into the prompt and re-apply any
    compaction so the LLM sees the compacted version.

    Compaction is deferred until after ALL messages have been replayed. This is
    critical because compact_messages() has a second pass that compacts tool
    results associated with compacted tool calls - those tool results appear
    later in the message list and must already be in the PromptManager when
    compaction runs.

    Args:
        messages: List of message dicts from session.json or session.messages
        session_manager: The SessionManager whose prompt_manager to populate
    """
    import json

    # Collect compaction requests to apply after all messages are replayed.
    # compact_messages() needs tool results to already be in the PromptManager
    # so it can compact them too, but tool results come after the assistant
    # message that triggered the compact call.
    deferred_compactions: list[tuple[str, str, str]] = []  # (from_id, to_id, summary)

    for msg in messages:
        role = msg.get("role")
        content = msg.get("content", "")

        # `_ui_only` strictly means "the LLM never sees this" (display-only
        # system notifications). Those were never appended to the PromptManager
        # at runtime, so skipping them here keeps replay IDs identical to
        # runtime. Auto-injected user messages that the LLM DOES see (inline-
        # error feedback) are marked `_synthetic`, NOT `_ui_only`, so they fall
        # through and are replayed — preserving the message-ID sequence that
        # stored compact ranges were captured against.
        if msg.get("_ui_only"):
            continue

        if role == "user":
            session_manager.append_user_message(content)
        elif role == "assistant":
            tool_calls = msg.get("tool_calls", [])
            if tool_calls:
                session_manager.append_tool_call(tool_calls, content)

                # Collect compaction requests for deferred application
                for tc in tool_calls:
                    func = tc.get("function", {})
                    if func.get("name") == "compact":
                        try:
                            args = json.loads(func.get("arguments", "{}"))
                            from_id = args.get("from_id", "")
                            to_id = args.get("to_id", "")
                            summary = args.get("summary", "")
                            if from_id and to_id:
                                deferred_compactions.append((from_id, to_id, summary))
                        except (json.JSONDecodeError, TypeError):
                            pass  # Malformed args, skip
            elif content:
                session_manager.append_assistant_message(content)
        elif role == "tool":
            tool_call_id = msg.get("tool_call_id", "")
            # Restore ephemeral status persisted on the message dict. Without
            # this, reloaded ephemeral tool results are never re-marked, so
            # expire_ephemeral_results() can never replace them with a
            # placeholder and they bloat context forever.
            is_ephemeral = bool(msg.get("_ephemeral", False))
            session_manager.append_tool_result(tool_call_id, content, is_ephemeral)

    # Re-embed output images referenced by historical assistant messages so the
    # model sees them again on reload. The full-quality + low-res copies were
    # already written to .forge/images/ when the message was first finalized;
    # here we just re-attach the model-facing low-res copy as an IMAGE_CONTENT
    # block. Gated on vision_enabled exactly like live embedding.
    _replay_embedded_images(messages, session_manager)

    # Now apply all compactions - all messages (including tool results) are in place
    print(
        f"🔁 REPLAY: applying {len(deferred_compactions)} deferred compaction(s): "
        f"{[(f, t) for f, t, _ in deferred_compactions]}"
    )
    for from_id, to_id, summary in deferred_compactions:
        compacted, error = session_manager.compact_messages(from_id, to_id, summary)
        if error:
            print(f"⚠️  REPLAY compaction error (#{from_id}-#{to_id}): {error}")
        else:
            print(f"📦 REPLAY compacted #{from_id}-#{to_id}: {compacted} block(s)")

    # Diagnostic: after replay + compaction, report any large conversation blocks
    # that were NOT compacted. This surfaces the replay bug where tool results
    # that used to be compacted are now left full-size in context.
    _log_uncompacted_large_blocks(session_manager)


def _log_uncompacted_large_blocks(
    session_manager: "SessionManager",
    min_tokens: int = 1000,
) -> None:
    """Log a summary of large conversation blocks left uncompacted after replay.

    Scans the prompt manager's live blocks and reports any USER_MESSAGE,
    ASSISTANT_MESSAGE, TOOL_CALL or TOOL_RESULT block whose estimated token
    count is >= ``min_tokens`` and whose content is NOT marked ``[COMPACTED``.

    Token estimate mirrors PromptManager (~3 chars/token). Each reported line
    includes the block's user-facing ID (message_id for messages/tool calls,
    user_id for tool results) so the offending block can be traced back to the
    stored compact ranges.
    """
    import json

    from forge.prompts.manager import BlockType

    prompt_manager = session_manager.prompt_manager
    conv_types = {
        BlockType.USER_MESSAGE,
        BlockType.ASSISTANT_MESSAGE,
        BlockType.TOOL_CALL,
        BlockType.TOOL_RESULT,
    }

    offenders: list[tuple[str, str, int]] = []  # (label, id, tokens)
    total_uncompacted_tokens = 0

    for block in prompt_manager.blocks:
        if block.deleted or block.block_type not in conv_types:
            continue
        if block.content.startswith("[COMPACTED"):
            continue

        tokens = len(block.content) // 3
        if block.block_type == BlockType.TOOL_CALL:
            # Tool call JSON is not in .content; count it too so big argument
            # payloads aren't undercounted.
            for tc in block.metadata.get("tool_calls", []):
                tokens += len(json.dumps(tc)) // 3

        if tokens < min_tokens:
            continue

        if block.block_type == BlockType.TOOL_RESULT:
            block_id = f"tool_result #{block.metadata.get('user_id', '?')}"
        else:
            block_id = f"msg #{block.metadata.get('message_id', '?')}"
        offenders.append((block.block_type.value, block_id, tokens))
        total_uncompacted_tokens += tokens

    if not offenders:
        print(f"✅ REPLAY: no uncompacted conversation blocks ≥{min_tokens} tokens after replay")
        return

    print(
        f"🔍 REPLAY: {len(offenders)} uncompacted conversation block(s) ≥{min_tokens} tokens "
        f"({total_uncompacted_tokens} tokens total left in context):"
    )
    for block_type, block_id, tokens in sorted(offenders, key=lambda o: o[2], reverse=True):
        print(f"   ↳ {block_id} ({block_type}): ~{tokens} tokens")


def _replay_embedded_images(
    messages: list[dict[str, Any]],
    session_manager: "SessionManager",
) -> None:
    """Re-attach model-facing IMAGE_CONTENT blocks for images embedded in
    historical assistant messages.

    When an assistant message was first finalized, output-embedded images were
    written to ``.forge/images/<sha>.<ext>`` (full quality) plus a
    ``.forge/images/<sha>.low.jpg`` sibling, and the low-res copy was attached
    as an IMAGE_CONTENT block so the model could see it. That block lives only
    in the in-memory prompt manager, so on reload we must recreate it from the
    files on disk. Gated on ``vision_enabled`` exactly like live embedding: if
    vision is off, the markdown still renders for the user (via the UI's
    embedded-image resolution) but nothing is replayed to the model.
    """
    if not session_manager.settings.get_vision_enabled():
        return

    import base64

    from forge.session.image_embedding import _low_res_sibling, find_embedded_image_refs

    vfs = session_manager.tool_manager.vfs
    prompt_manager = session_manager.prompt_manager

    for msg in messages:
        if msg.get("_ui_only") or msg.get("role") != "assistant":
            continue
        content = msg.get("content", "") or ""
        for full_path in find_embedded_image_refs(content):
            low_path = _low_res_sibling(full_path)
            try:
                if not vfs.file_exists(low_path):
                    continue
                low_bytes = vfs.read_file_bytes(low_path)
            except (FileNotFoundError, OSError):
                continue
            b64 = base64.b64encode(low_bytes).decode("ascii")
            data_url = f"data:image/jpeg;base64,{b64}"
            prompt_manager.append_image_content(full_path, data_url, embedded=True)


def load_or_create_session(
    repo: "ForgeRepository",
    branch_name: str,
    settings: "Settings",
) -> "LiveSession | None":
    """
    Load a session from disk and create a LiveSession for it.

    If the session doesn't exist, returns None.
    If the session exists, creates a LiveSession (but doesn't start it).

    The session IS registered with SESSION_REGISTRY.

    Args:
        repo: The git repository
        branch_name: Branch to load session from
        settings: Application settings

    Returns:
        LiveSession if session exists, None otherwise
    """
    from forge.session.registry import SESSION_REGISTRY

    return SESSION_REGISTRY.load(branch_name, repo, settings)


def start_or_resume_session(
    repo: "ForgeRepository",
    branch_name: str,
    settings: "Settings",
    message: str | None = None,
) -> "LiveSession | None":
    """
    Load a session and optionally start it with a message.

    This is the main entry point for starting child sessions.
    It handles:
    - Loading from disk if not in registry
    - Using existing session if in registry
    - Sending the start message

    Args:
        repo: The git repository
        branch_name: Branch to start session on
        settings: Application settings
        message: Optional message to send (starts the session)

    Returns:
        The LiveSession (new or existing), or None if session doesn't exist
    """
    from forge.session.registry import SESSION_REGISTRY

    # Load (or get existing)
    session = SESSION_REGISTRY.load(branch_name, repo, settings)
    if not session:
        return None

    was_newly_loaded = session.state == "idle" and not session.messages

    # Start the session if message provided
    # If newly loaded with messages already, the message is in session.json
    # so we just need to trigger the run without adding another message
    if message:
        if was_newly_loaded and session.messages:
            # Message already in session.json, just trigger the run
            session.send_message("", _trigger_only=True)
        else:
            session.send_message(message)

    return session


def get_recoverable_sessions(repo: "ForgeRepository") -> list[dict[str, Any]]:
    """
    Find sessions that may need recovery (were running when app closed).

    Returns list of dicts with:
    - branch_name: str
    - state: str (the stored state)
    - yield_message: str | None
    - parent_session: str | None

    Used by application startup to show recovery options.
    """
    import json

    from forge.constants import SESSION_BRANCH_PREFIX, SESSION_FILE

    recoverable = []

    for branch_name in repo.repo.branches.local:
        # Only check session branches
        if not branch_name.startswith(SESSION_BRANCH_PREFIX):
            continue

        try:
            content = repo.get_file_content(SESSION_FILE, branch_name)
            session_data = json.loads(content)

            state = session_data.get("state", "idle")

            # Sessions that were actively running or waiting are recoverable
            if state in ("running", "waiting_children", "waiting_input"):
                # Normalize "running" to "idle" - we're not actually running after restart
                display_state = "idle" if state == "running" else state
                recoverable.append(
                    {
                        "branch_name": branch_name,
                        "state": display_state,
                        "yield_message": session_data.get("yield_message"),
                        "parent_session": session_data.get("parent_session"),
                    }
                )
        except (FileNotFoundError, KeyError, json.JSONDecodeError):
            continue

    return recoverable


# Backwards compatibility
load_or_create_runner = load_or_create_session
