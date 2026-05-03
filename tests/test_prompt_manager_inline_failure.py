"""
Flow test: after an inline-command pipeline fails on a `<run_tests/>` step
that came AFTER successful `<replace>` edits, the AI's next prompt must:

  1. Show the NEW content of the edited files.
  2. NOT show the OLD pre-edit content as live (non-tombstone) content.
  3. Have exactly one live FILE_CONTENT block per edited file.

Driven through the session harness so the test exercises the real
LiveSession pipeline (inline execution → flush pending file updates),
not just the PromptManager in isolation.
"""

from __future__ import annotations

from forge.prompts.manager import BlockType


# Distinctive markers so substring searches are unambiguous.
A_OLD = "FOO_RETURNS_ONE_v_old_marker"
A_NEW = "FOO_RETURNS_TWO_v_new_marker"
B_OLD = "BAR_RETURNS_TEN_v_old_marker"
B_NEW = "BAR_RETURNS_TWENTY_v_new_marker"


def _setup_failed_pipeline(session):
    """Common setup: two files in context, AI does edits + failing run_tests."""
    session.given_files(
        {
            "a.py": f"def foo():\n    return 1  # {A_OLD}\n",
            "b.py": f"def bar():\n    return 10  # {B_OLD}\n",
        }
    )
    session.given_files_in_context("a.py", "b.py")
    session.given_failing_tests()

    session.user_says("Fix foo to return 2 and bar to return 20, then run tests.")
    session.ai_says(
        f"""
        I'll fix both functions.

        @edit a.py
            old:
                def foo():
                    return 1  # {A_OLD}
            new:
                def foo():
                    return 2  # {A_NEW}

        @edit b.py
            old:
                def bar():
                    return 10  # {B_OLD}
            new:
                def bar():
                    return 20  # {B_NEW}

        @run_tests

        Both functions are updated; tests should now pass.
        """
    )
    return session.run_turn()


def test_new_file_content_present_in_prompt_after_failed_pipeline(session):
    """The AI must see the NEW content of edited files after a failed pipeline."""
    result = _setup_failed_pipeline(session)
    assert result.failed_at == "run_tests"

    text = session.next_prompt_text()
    assert A_NEW in text, (
        "a.py's NEW content is missing from the prompt sent to the AI. "
        "The AI will think its edits were not applied."
    )
    assert B_NEW in text, "b.py's NEW content is missing from the prompt sent to the AI."


def test_old_file_content_not_visible_as_live_content(session):
    """The OLD pre-edit content must NOT appear as live content anywhere.

    It's OK if A_OLD appears inside a tombstone block (the tombstone text
    doesn't include the file content — it's a placeholder), but the
    pre-edit source code itself must not be present in any non-tombstone
    block.
    """
    result = _setup_failed_pipeline(session)
    assert result.failed_at == "run_tests"

    for block in session.prompt_blocks:
        if block.deleted:
            continue
        if block.block_type != BlockType.FILE_CONTENT:
            continue
        if block.metadata.get("tombstone"):
            assert A_OLD not in block.content, (
                f"Tombstone for {block.metadata.get('filepath')} still contains "
                f"the OLD content marker — it should be a bare placeholder."
            )
            assert B_OLD not in block.content, (
                f"Tombstone for {block.metadata.get('filepath')} still contains "
                f"the OLD content marker."
            )
            continue
        assert A_OLD not in block.content, (
            f"File block for {block.metadata.get('filepath')} still contains "
            f"the OLD content for a.py — duplicate stale block."
        )
        assert B_OLD not in block.content, (
            f"File block for {block.metadata.get('filepath')} still contains "
            f"the OLD content for b.py — duplicate stale block."
        )

    # Note: we deliberately do NOT scan the full rendered prompt text for
    # OLD markers here. The assistant's own message — which we echo back
    # — legitimately contains the OLD markers inside its <old> blocks.
    # That's the AI's own words, not a leaked stale FILE_CONTENT block.
    # The block-level loop above is what enforces "no stale live blocks".


def test_each_edited_file_appears_exactly_once_as_live_content(session):
    """Each edited file should have exactly one live FILE_CONTENT block."""
    result = _setup_failed_pipeline(session)
    assert result.failed_at == "run_tests"

    live_blocks_by_file: dict[str, list] = {}
    for block in session.prompt_blocks:
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
        f"a.py has {len(live_blocks_by_file['a.py'])} live file blocks, expected 1"
    )
    assert len(live_blocks_by_file["b.py"]) == 1, (
        f"b.py has {len(live_blocks_by_file['b.py'])} live file blocks, expected 1"
    )

    a_block = live_blocks_by_file["a.py"][0]
    b_block = live_blocks_by_file["b.py"][0]
    assert A_NEW in a_block.content, "a.py's live block does not hold NEW content"
    assert B_NEW in b_block.content, "b.py's live block does not hold NEW content"