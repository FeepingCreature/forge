"""
Test what the AI's next prompt looks like after an inline-command pipeline
fails on a `<run_tests/>` step that came AFTER successful `<replace>` edits.

This drives PromptManager directly — the same way LiveSession's
`_flush_pending_file_updates` does — to verify that:

  1. The new file content appears in the prompt sent to the AI.
  2. The OLD file content does NOT appear anywhere as live (non-tombstone)
     content. The AI must not see the pre-edit version of the file.
  3. The tombstone for the old block is present (so the AI knows the
     content was relocated, not silently deleted).

The user's bug report: "the AI's next response acts like it was shown the
old edits". If that's a PromptManager-level bug, this test should fail.
"""

from forge.prompts.manager import BlockType, PromptManager


# Distinctive markers so substring searches are unambiguous.
A_OLD = "FOO_RETURNS_ONE_v_old_marker"
A_NEW = "FOO_RETURNS_TWO_v_new_marker"
B_OLD = "BAR_RETURNS_TEN_v_old_marker"
B_NEW = "BAR_RETURNS_TWENTY_v_new_marker"


def _all_user_text(messages: list[dict]) -> str:
    """Concatenate every user-role text block into one big string."""
    parts: list[str] = []
    for msg in messages:
        if msg.get("role") != "user":
            continue
        content = msg.get("content")
        if isinstance(content, str):
            parts.append(content)
        elif isinstance(content, list):
            for blk in content:
                if isinstance(blk, dict) and "text" in blk:
                    parts.append(blk["text"])
    return "\n".join(parts)


def _build_session_with_files_in_context() -> PromptManager:
    """
    Build a PromptManager whose state mirrors a session in which the
    user has already opened a.py and b.py into context (so their old
    contents are present in the prompt) and asked the AI to fix them.
    """
    pm = PromptManager(system_prompt="System")

    # Simulate "files already in context" — these were appended at session
    # start (e.g. by add_active_file) with their pre-edit contents.
    pm.append_file_content("a.py", f"def foo():\n    return 1  # {A_OLD}\n")
    pm.append_file_content("b.py", f"def bar():\n    return 10  # {B_OLD}\n")

    # User asks the AI to fix both functions.
    pm.append_user_message("Fix foo to return 2 and bar to return 20, then run tests.")

    return pm


def _simulate_failed_inline_pipeline(pm: PromptManager) -> None:
    """
    Simulate what LiveSession does after the inline pipeline
    [<replace a.py>, <replace b.py>, <run_tests/>] fails on run_tests.

    Mirrors the order of operations in _on_inline_commands_finished
    (failure branch) → _continue_after_tools → _process_llm_request:

      1. Update last assistant message with the annotated content
         (already done by `update_last_assistant_message` upstream;
         we just append the assistant message here directly since
         we're starting from a state with no prior assistant turn).
      2. Append "❌ run_tests failed" as a user-visible system message.
      3. _flush_pending_file_updates: for each successful inline edit,
         call file_was_modified(filepath, None) which calls
         append_file_content(filepath, new_content, tool_call_id=None).
    """
    # Step 1: assistant message with edits + run_tests + trailing narration,
    # with the pipeline error injected after <run_tests/>.
    annotated = (
        '<replace file="a.py">\n<old>...\n</old>\n<new>...\n</new>\n</replace>\n\n'
        '<replace file="b.py">\n<old>...\n</old>\n<new>...\n</new>\n</replace>\n\n'
        "<run_tests/>\n"
        "[INLINE COMMAND ERROR: run_tests failed: simulated test failure]\n"
        "Both functions are updated and the tests should now pass.\n"
    )
    pm.append_assistant_message(annotated)

    # Step 2: user-visible error message.
    pm.append_user_message("❌ run_tests failed: simulated test failure")

    # Step 3: flush pending file updates (the new content from the VFS).
    # This is the critical step — exactly what _flush_pending_file_updates does.
    pm.append_file_content("a.py", f"def foo():\n    return 2  # {A_NEW}\n")
    pm.append_file_content("b.py", f"def bar():\n    return 20  # {B_NEW}\n")


def test_new_file_content_present_in_prompt_after_failed_pipeline():
    """The AI must see the NEW content of edited files after a failed pipeline."""
    pm = _build_session_with_files_in_context()
    _simulate_failed_inline_pipeline(pm)

    text = _all_user_text(pm.to_messages())

    assert A_NEW in text, (
        "a.py's NEW content is missing from the prompt sent to the AI. "
        "The AI will think its edits were not applied."
    )
    assert B_NEW in text, (
        "b.py's NEW content is missing from the prompt sent to the AI."
    )


def test_old_file_content_not_visible_as_live_content():
    """
    The OLD pre-edit content must NOT appear as live content anywhere.

    It's OK if A_OLD appears inside a tombstone block (the tombstone text
    doesn't include the file content — it's a placeholder), but the
    pre-edit source code itself must not be present in any non-tombstone
    block. If it is, the AI sees the file twice (old + new) and may
    attend to the old version.
    """
    pm = _build_session_with_files_in_context()
    _simulate_failed_inline_pipeline(pm)

    # Inspect blocks directly — tombstones should NOT contain the old
    # content (tombstones are bare placeholders).
    for block in pm.blocks:
        if block.deleted:
            continue
        if block.block_type != BlockType.FILE_CONTENT:
            continue
        if block.metadata.get("tombstone"):
            assert A_OLD not in block.content, (
                f"Tombstone for {block.metadata.get('filepath')} still "
                f"contains the OLD content marker — it should be a bare "
                f"placeholder. Tombstone content: {block.content!r}"
            )
            assert B_OLD not in block.content, (
                f"Tombstone for {block.metadata.get('filepath')} still "
                f"contains the OLD content marker."
            )
            continue
        # Non-tombstone, non-deleted file block: must not contain old markers.
        assert A_OLD not in block.content, (
            f"File block for {block.metadata.get('filepath')} still "
            f"contains the OLD content for a.py — duplicate stale block."
        )
        assert B_OLD not in block.content, (
            f"File block for {block.metadata.get('filepath')} still "
            f"contains the OLD content for b.py — duplicate stale block."
        )

    # And cross-check via the rendered prompt: the OLD markers must not
    # be in the messages sent to the AI at all.
    text = _all_user_text(pm.to_messages())
    assert A_OLD not in text, (
        "a.py's OLD content (pre-edit) is still present in the prompt "
        "sent to the AI. The AI will see two versions and may believe "
        "the edit didn't take effect."
    )
    assert B_OLD not in text, (
        "b.py's OLD content (pre-edit) is still present in the prompt."
    )


def test_each_edited_file_appears_exactly_once_as_live_content():
    """
    Each edited file should have exactly one live (non-tombstone,
    non-deleted) FILE_CONTENT block, holding the NEW content.
    """
    pm = _build_session_with_files_in_context()
    _simulate_failed_inline_pipeline(pm)

    live_blocks_by_file: dict[str, list] = {}
    for block in pm.blocks:
        if block.deleted:
            continue
        if block.block_type != BlockType.FILE_CONTENT:
            continue
        if block.metadata.get("tombstone"):
            continue
        fp = block.metadata.get("filepath")
        live_blocks_by_file.setdefault(fp, []).append(block)

    assert "a.py" in live_blocks_by_file, "a.py has no live file block"
    assert "b.py" in live_blocks_by_file, "b.py has no live file block"

    assert len(live_blocks_by_file["a.py"]) == 1, (
        f"a.py has {len(live_blocks_by_file['a.py'])} live file blocks, "
        f"expected exactly 1"
    )
    assert len(live_blocks_by_file["b.py"]) == 1, (
        f"b.py has {len(live_blocks_by_file['b.py'])} live file blocks, "
        f"expected exactly 1"
    )

    # The single live block must hold the NEW content.
    assert A_NEW in live_blocks_by_file["a.py"][0].content
    assert B_NEW in live_blocks_by_file["b.py"][0].content


def test_new_content_appears_after_run_tests_error_in_prompt():
    """
    The new file content (which represents the actual VFS state after the
    edits) must appear AFTER the assistant's <run_tests/> message and the
    "❌ run_tests failed" user message in the prompt stream — otherwise
    the AI may interpret it as the pre-failure state.
    """
    pm = _build_session_with_files_in_context()
    _simulate_failed_inline_pipeline(pm)

    # Find positions in the rendered text of: the failure marker, A_NEW, B_NEW.
    text = _all_user_text(pm.to_messages())
    failure_pos = text.find("run_tests failed")
    a_new_pos = text.find(A_NEW)
    b_new_pos = text.find(B_NEW)

    assert failure_pos >= 0, "run_tests failure marker missing from prompt"
    assert a_new_pos >= 0, "A_NEW marker missing from prompt"
    assert b_new_pos >= 0, "B_NEW marker missing from prompt"

    assert a_new_pos > failure_pos, (
        "a.py's new content appears BEFORE the run_tests failure message "
        "in the prompt. The AI may read it as pre-failure state."
    )
    assert b_new_pos > failure_pos, (
        "b.py's new content appears BEFORE the run_tests failure message."
    )