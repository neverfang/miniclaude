from langchain_core.messages import AIMessage, HumanMessage, RemoveMessage

from miniclaude.core.state import RuntimeState, ToolError
from miniclaude.graph.context import CompressionOutput, make_context_compressor_node
from miniclaude.graph.state import initial_graph_state


class StructuredModel:
    def __init__(self, output=None, error=None):
        self.output = output
        self.error = error
        self.method = None
        self.inputs = []

    def with_structured_output(self, schema, **kwargs):
        self.method = kwargs.get("method")
        self.schema = schema
        return self

    def invoke(self, messages):
        self.inputs.append(messages)
        if self.error:
            raise self.error
        return self.output


class SequenceCounter:
    def __init__(self, *counts):
        self.counts = iter(counts)

    def get_num_tokens_from_messages(self, messages):
        return next(self.counts)


def _output():
    return CompressionOutput(
        summary="Implementation is in progress.",
        active_goal="Finish context engineering.",
        completed_work=["Added token monitor."],
        open_todos=["Add compressor tests."],
        important_files=["src/miniclaude/graph/context.py"],
        tool_findings=["Offline tests passed."],
        sources=["https://example.com/docs"],
        next_steps=["Run verifier."],
        risks=["Provider output is untrusted."],
    )


def _state(tmp_path):
    state = initial_graph_state("build stage four", runtime=RuntimeState(tmp_path))
    state["messages"] = [HumanMessage(content="old context")]
    state["context_token_count"] = 900
    state["context_token_limit"] = 10_000
    state["memory_snapshot"] = {
        "rules": {"rules": ["verify claims"]},
        "working_memory": {"task": "build stage four"},
        "history_summary_store": {},
    }
    return state


def test_compressor_replaces_messages_and_persists_structured_summary(tmp_path):
    state = _state(tmp_path)
    model = StructuredModel(_output())
    events = []

    update = make_context_compressor_node(model, SequenceCounter(120), emit=events.append)(state)

    assert model.method == "function_calling"
    assert isinstance(update["messages"][0], RemoveMessage)
    assert update["messages"][0].id == "__remove_all__"
    assert isinstance(update["messages"][1], AIMessage)
    summary = update["messages"][1].content
    for heading in (
        "Summary",
        "Active Goal",
        "Completed Work",
        "Open Todos",
        "Important Files",
        "Tool Findings",
        "Sources",
        "Next Steps",
        "Risks",
    ):
        assert f"## {heading}" in summary
    assert (tmp_path / "HISTORY_SUMMARY.md").read_text(encoding="utf-8") == summary
    assert update["history_summary"] == summary
    assert update["context_summary"] == summary
    assert update["context_token_count"] == 120
    assert update["context_next_node"] == "supervisor"
    assert update["compression_events"][-1]["used_fallback"] is False
    assert events[-1]["type"] == "context_compressor"


def test_compressor_uses_sanitized_local_fallback_on_provider_failure(tmp_path):
    state = _state(tmp_path)
    state["task"] = "use DEEPSEEK_API_KEY=private-secret-value"
    model = StructuredModel(error=RuntimeError("Bearer private-secret-value"))

    update = make_context_compressor_node(model, SequenceCounter(50))(state)

    summary = update["messages"][1].content
    assert "private-secret-value" not in summary
    assert "private-secret-value" not in update["context_error"]
    assert "[REDACTED]" in summary
    assert update["compression_events"][-1]["used_fallback"] is True


def test_compressor_uses_fallback_for_invalid_or_blank_output(tmp_path):
    state = _state(tmp_path)
    model = StructuredModel({"summary": " "})

    update = make_context_compressor_node(model, SequenceCounter(40))(state)

    assert update["compression_events"][-1]["used_fallback"] is True
    assert "## Active Goal" in update["context_summary"]


def test_compressor_keeps_memory_when_history_persistence_fails(tmp_path, monkeypatch):
    state = _state(tmp_path)

    def fail_persist(runtime, summary):
        raise ToolError("Bearer private-secret-value")

    monkeypatch.setattr("miniclaude.graph.context.persist_history_summary", fail_persist)

    update = make_context_compressor_node(StructuredModel(_output()), SequenceCounter(60))(state)

    assert update["context_summary"]
    assert update["history_summary"] == update["context_summary"]
    event = update["compression_events"][-1]
    assert "private-secret-value" not in event["persistence_error"]
    assert event["persistence_error"]


def test_compressor_uses_minimal_recovery_if_first_replacement_is_too_large(tmp_path):
    state = _state(tmp_path)
    state["context_token_limit"] = 100

    update = make_context_compressor_node(StructuredModel(_output()), SequenceCounter(200, 10))(
        state
    )

    assert update["context_token_count"] == 10
    assert update["context_should_compress"] is False
    assert update["compression_events"][-1]["used_fallback"] is True
    assert "Minimal Context Recovery" in update["context_summary"]


def test_compressor_stops_after_three_still_oversized_cycles(tmp_path):
    state = _state(tmp_path)
    state["context_token_limit"] = 100
    node = make_context_compressor_node(
        StructuredModel(_output()), SequenceCounter(200, 200, 200, 200, 200, 200)
    )

    for _ in range(3):
        update = node(state)
        state.update(update)
        state["messages"] = [update["messages"][-1]]

    assert len(state["compression_events"]) == 3
    assert state["context_should_compress"] is True
    assert state["context_next_node"] == "final"
    assert "three" in state["context_error"].casefold()
