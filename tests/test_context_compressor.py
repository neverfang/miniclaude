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


def test_compressor_rejects_blank_structured_list_items(tmp_path):
    state = _state(tmp_path)
    output = _output().model_dump()
    output["open_todos"] = [" "]

    update = make_context_compressor_node(StructuredModel(output), SequenceCounter(40))(state)

    assert update["compression_events"][-1]["used_fallback"] is True


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


def test_compressor_uses_minimal_recovery_if_summary_does_not_reduce_context(tmp_path):
    state = _state(tmp_path)

    update = make_context_compressor_node(StructuredModel(_output()), SequenceCounter(950, 20))(
        state
    )

    assert "Minimal Context Recovery" in update["context_summary"]
    assert update["context_token_count"] == 20


def test_compressor_fails_immediately_if_minimal_recovery_is_still_oversized(tmp_path):
    state = _state(tmp_path)
    state["context_token_limit"] = 100

    update = make_context_compressor_node(StructuredModel(_output()), SequenceCounter(200, 200))(
        state
    )

    assert update["context_should_compress"] is True
    assert update["context_next_node"] == "final"
    assert "minimal" in update["context_error"].casefold()


def test_compressor_trims_long_state_fields_and_keeps_durable_fallback_evidence(tmp_path):
    state = _state(tmp_path)
    state["research_notes"] = "r" * 3_000
    state["supervisor_summary"] = "s" * 3_000
    state["code_agent_summary"] = "implemented app.py"
    state["agent_handoffs"] = [
        {
            "from_agent": "planner",
            "to_agent": "codeAgent",
            "instruction": "i" * 2_000,
            "result": "changed app.py",
            "ok": True,
        }
        for _ in range(10)
    ]
    state["memory_snapshot"]["history_summary_store"] = {
        "history_summary": "prior durable history",
        "notepad": "durable decision",
    }

    update = make_context_compressor_node(
        StructuredModel(error=RuntimeError("offline")), SequenceCounter(100)
    )(state)

    assert len(update["research_notes"]) <= 1_203
    assert len(update["supervisor_summary"]) <= 1_003
    assert len(update["agent_handoffs"]) == 6
    assert len(update["agent_handoffs"][0]["instruction"]) <= 603
    assert "implemented app.py" in update["context_summary"]
    assert "prior durable history" in update["context_summary"]
    assert "durable decision" in update["context_summary"]
