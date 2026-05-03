"""Tests for forge.runtime.llm_backend.

Covers ScriptedBackend (the test impl) directly and verifies
OpenRouterBackend's chunk → event translation against fake LLMClient
data.
"""

import pytest

from forge.runtime import (
    OpenRouterBackend,
    ScriptedBackend,
    StreamChunk,
    StreamFinished,
    StreamToolCallDelta,
)


# --- ScriptedBackend ---


class TestScriptedBackendBasics:
    def test_queued_text_response_yields_finished_event(self) -> None:
        backend = ScriptedBackend()
        backend.queue_response(content="hello world")

        events = list(backend.stream(messages=[], tools=None))

        assert len(events) == 1
        assert isinstance(events[0], StreamFinished)
        assert events[0].content == "hello world"
        assert events[0].tool_calls is None

    def test_chunk_size_splits_content_into_stream_chunks(self) -> None:
        backend = ScriptedBackend()
        backend.queue_response(content="abcdef", chunk_size=2)

        events = list(backend.stream(messages=[], tools=None))
        chunks = [e for e in events if isinstance(e, StreamChunk)]
        finals = [e for e in events if isinstance(e, StreamFinished)]

        assert [c.text for c in chunks] == ["ab", "cd", "ef"]
        assert len(finals) == 1
        assert finals[0].content == "abcdef"

    def test_tool_call_response_emits_delta_then_finished(self) -> None:
        backend = ScriptedBackend()
        tc = {
            "id": "call_1",
            "type": "function",
            "function": {"name": "edit", "arguments": '{"path": "a.py"}'},
        }
        backend.queue_response(tool_calls=[tc])

        events = list(backend.stream(messages=[], tools=None))

        deltas = [e for e in events if isinstance(e, StreamToolCallDelta)]
        finals = [e for e in events if isinstance(e, StreamFinished)]
        assert len(deltas) == 1
        assert deltas[0].index == 0
        assert deltas[0].tool_call == tc
        assert finals[0].tool_calls == [tc]

    def test_responses_are_consumed_in_order(self) -> None:
        backend = ScriptedBackend()
        backend.queue_response(content="first")
        backend.queue_response(content="second")

        first = [e for e in backend.stream([], None) if isinstance(e, StreamFinished)]
        second = [e for e in backend.stream([], None) if isinstance(e, StreamFinished)]

        assert first[0].content == "first"
        assert second[0].content == "second"

    def test_error_response_raises(self) -> None:
        backend = ScriptedBackend()
        backend.queue_error("rate limited")

        with pytest.raises(RuntimeError, match="rate limited"):
            list(backend.stream([], None))


class TestScriptedBackendStrictness:
    def test_assert_drained_passes_when_all_consumed(self) -> None:
        backend = ScriptedBackend()
        backend.queue_response(content="ok")
        list(backend.stream([], None))
        backend.assert_drained()  # must not raise

    def test_assert_drained_fails_with_unconsumed_response(self) -> None:
        backend = ScriptedBackend()
        backend.queue_response(content="never used")

        with pytest.raises(AssertionError, match="unconsumed"):
            backend.assert_drained()

    def test_optional_responses_dont_trigger_assert_drained(self) -> None:
        backend = ScriptedBackend()
        backend.queue_response(content="optional one", optional=True)

        backend.assert_drained()  # must not raise

    def test_mixed_optional_and_required(self) -> None:
        backend = ScriptedBackend()
        backend.queue_response(content="required", optional=False)
        backend.queue_response(content="optional", optional=True)

        with pytest.raises(AssertionError):
            backend.assert_drained()

        # consume the required one; now drained passes (optional may stay)
        list(backend.stream([], None))
        backend.assert_drained()

    def test_calling_stream_with_empty_queue_yields_empty_finished(self) -> None:
        # Friendlier than raising: the session pipeline's _on_stream_error
        # path retries on exception, which would infinite-loop under
        # SyncTaskRunner. assert_drained() at fixture teardown is what
        # catches under-queueing instead.
        backend = ScriptedBackend()

        events = list(backend.stream([], None))

        assert len(events) == 1
        assert isinstance(events[0], StreamFinished)
        assert events[0].content is None
        assert events[0].tool_calls is None


class TestScriptedBackendInspection:
    def test_stream_calls_records_arguments(self) -> None:
        backend = ScriptedBackend()
        backend.queue_response(content="ok")
        msgs = [{"role": "user", "content": "hi"}]
        tools = [{"name": "edit"}]

        list(backend.stream(msgs, tools))

        assert len(backend.stream_calls) == 1
        recorded_msgs, recorded_tools = backend.stream_calls[0]
        assert recorded_msgs == msgs
        assert recorded_tools == tools

    def test_stream_calls_snapshot_isolated_from_caller_mutation(self) -> None:
        backend = ScriptedBackend()
        backend.queue_response(content="ok")
        msgs = [{"role": "user", "content": "hi"}]

        list(backend.stream(msgs, None))
        msgs.append({"role": "user", "content": "second"})

        # The recorded call must not reflect the later mutation.
        recorded_msgs, _ = backend.stream_calls[0]
        assert len(recorded_msgs) == 1


# --- OpenRouterBackend ---


class _FakeLLMClient:
    """Stand-in for LLMClient.chat_stream — yields raw OpenRouter chunks."""

    def __init__(self, chunks: list[dict]) -> None:
        self._chunks = chunks

    def chat_stream(self, messages, tools):  # noqa: ANN001
        yield from self._chunks


class TestOpenRouterBackend:
    def test_text_chunks_become_stream_chunks(self) -> None:
        client = _FakeLLMClient(
            [
                {"choices": [{"delta": {"content": "hel"}}]},
                {"choices": [{"delta": {"content": "lo"}}]},
            ]
        )
        backend = OpenRouterBackend(client)

        events = list(backend.stream([], None))

        chunks = [e for e in events if isinstance(e, StreamChunk)]
        finals = [e for e in events if isinstance(e, StreamFinished)]
        assert [c.text for c in chunks] == ["hel", "lo"]
        assert finals[0].content == "hello"
        assert finals[0].tool_calls is None

    def test_tool_call_assembly_across_fragments(self) -> None:
        # Tool call arguments arrive in pieces; the backend joins them.
        client = _FakeLLMClient(
            [
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "call_1",
                                        "function": {"name": "edit", "arguments": '{"p":'},
                                    }
                                ]
                            }
                        }
                    ]
                },
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {"index": 0, "function": {"arguments": '"a.py"}'}}
                                ]
                            }
                        }
                    ]
                },
            ]
        )
        backend = OpenRouterBackend(client)

        events = list(backend.stream([], None))
        finals = [e for e in events if isinstance(e, StreamFinished)]

        assert len(finals) == 1
        assert finals[0].tool_calls == [
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "edit", "arguments": '{"p":"a.py"}'},
            }
        ]

    def test_chunks_without_choices_are_skipped(self) -> None:
        # OpenRouter sends some metadata-only chunks with no choices.
        client = _FakeLLMClient(
            [
                {"id": "gen_1"},
                {"choices": []},
                {"choices": [{"delta": {"content": "ok"}}]},
            ]
        )
        backend = OpenRouterBackend(client)

        events = list(backend.stream([], None))
        finals = [e for e in events if isinstance(e, StreamFinished)]
        assert finals[0].content == "ok"

    def test_empty_stream_yields_only_finished_with_nones(self) -> None:
        backend = OpenRouterBackend(_FakeLLMClient([]))

        events = list(backend.stream([], None))

        assert len(events) == 1
        assert isinstance(events[0], StreamFinished)
        assert events[0].content is None
        assert events[0].tool_calls is None