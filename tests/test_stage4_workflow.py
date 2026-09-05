from langchain_core.messages import AIMessage

from miniclaude.core.state import RuntimeState
from miniclaude.graph.stage4_workflow import build_stage4_workflow
from miniclaude.graph.state import initial_graph_state


class SequenceModel:
    def __init__(self, replies, structured_replies=None):
        self.replies = iter(replies)
        self.structured_replies = iter(structured_replies or [])

    def bind_tools(self, tools):
        return self

    def invoke(self, messages):
        return next(self.replies)

    def with_structured_output(self, schema, **kwargs):
        parent = self

        class Runner:
            def invoke(self, messages):
                return next(parent.structured_replies)

        return Runner()


class SequenceCounter:
    def __init__(self, *counts):
        self.counts = iter(counts)

    def get_num_tokens_from_messages(self, messages):
        return next(self.counts)


def call(name, args, ident):
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": ident}])


def supervisor_round(suffix=""):
    return [
        call(
            "TodoWriteTool",
            {
                "plan_summary": "implement and verify",
                "todos": [{"id": "code", "content": "implement"}],
                "acceptance_criteria": ["result exists"],
                "verification_commands": [],
            },
            f"p{suffix}",
        ),
        call("CallCodeAgentTool", {"instruction": "implement"}, f"c{suffix}"),
        AIMessage(content="supervision complete"),
    ]


def verdict(passed, reason):
    return {
        "passed": passed,
        "reason": reason,
        "checks": [{"name": "result exists", "passed": passed, "detail": reason}],
        "recommended_next_instruction": "repair" if not passed else "",
    }


def compression_output():
    return {
        "summary": "Work is ready to verify.",
        "active_goal": "Finish the task.",
        "completed_work": ["Implemented files."],
        "open_todos": [],
        "important_files": ["app.py"],
        "tool_findings": ["Tests passed."],
        "sources": [],
        "next_steps": ["Verify."],
        "risks": ["Claims are untrusted."],
    }


def code_runner(snapshot, instruction, **kwargs):
    todos = snapshot["todos"]
    todos[0]["status"] = "completed"
    return {
        "ok": True,
        "summary": "implemented",
        "todos": todos,
        "messages": [],
        "tool_events": [],
    }


def build_graph(supervisor, verifier, counter):
    return build_stage4_workflow(
        supervisor_model=supervisor,
        verifier_model=verifier,
        web_search_tool=object(),
        context_counter=counter,
        code_runner=code_runner,
    )


def test_stage4_success_builds_memory_and_skips_compression(tmp_path):
    graph = build_graph(
        SequenceModel(supervisor_round()),
        SequenceModel([AIMessage(content="inspected")], [verdict(True, "good")]),
        SequenceCounter(20),
    )
    state = initial_graph_state("build", runtime=RuntimeState(tmp_path), max_attempts=1)

    result = graph.invoke(state)

    assert result["passed"] is True
    assert result["attempts"] == 1
    assert result["context_token_count"] == 20
    assert result["compression_events"] == []
    assert result["memory_snapshot"]["working_memory"]["task"] == "build"


def test_stage4_compresses_then_resumes_through_supervisor(tmp_path):
    supervisor = SequenceModel(
        supervisor_round("1") + supervisor_round("2"),
        [compression_output()],
    )
    verifier = SequenceModel([AIMessage(content="inspected")], [verdict(True, "good")])
    graph = build_graph(supervisor, verifier, SequenceCounter(100, 20, 20))
    state = initial_graph_state("build", runtime=RuntimeState(tmp_path), max_attempts=1)
    state["context_token_limit"] = 100

    result = graph.invoke(state)

    assert result["passed"] is True
    assert result["attempts"] == 1
    assert len(result["compression_events"]) == 1
    assert result["compression_events"][0]["after_tokens"] == 20
    assert (tmp_path / "HISTORY_SUMMARY.md").exists()


def test_stage4_retries_supervisor_after_failed_verification(tmp_path):
    supervisor = SequenceModel(supervisor_round("1") + supervisor_round("2"))
    verifier = SequenceModel(
        [AIMessage(content="inspect 1"), AIMessage(content="inspect 2")],
        [verdict(False, "missing"), verdict(True, "fixed")],
    )
    graph = build_graph(supervisor, verifier, SequenceCounter(10, 10))
    state = initial_graph_state("build", runtime=RuntimeState(tmp_path), max_attempts=2)

    result = graph.invoke(state)

    assert result["passed"] is True
    assert result["attempts"] == 2


def test_stage4_stops_at_max_attempts(tmp_path):
    graph = build_graph(
        SequenceModel(supervisor_round()),
        SequenceModel([AIMessage(content="inspect")], [verdict(False, "broken")]),
        SequenceCounter(10),
    )
    state = initial_graph_state("build", runtime=RuntimeState(tmp_path), max_attempts=1)

    result = graph.invoke(state)

    assert result["passed"] is False
    assert result["attempts"] == 1
    assert "not verified" in result["final_answer"]


def test_stage4_compression_failure_is_bounded_without_attempt_increment(tmp_path):
    class FailingStructuredModel(SequenceModel):
        def with_structured_output(self, schema, **kwargs):
            class Runner:
                def invoke(self, messages):
                    raise RuntimeError("invalid output")

            return Runner()

    supervisor = FailingStructuredModel(supervisor_round())
    verifier = SequenceModel([], [])
    graph = build_graph(supervisor, verifier, SequenceCounter(200, 200, 200))
    state = initial_graph_state("build", runtime=RuntimeState(tmp_path), max_attempts=1)
    state["context_token_limit"] = 100
    state["compression_events"] = [
        {
            "before_tokens": 200,
            "after_tokens": 200,
            "removed_messages": 2,
            "attempt": index,
            "used_fallback": True,
            "limit": 100,
        }
        for index in (1, 2)
    ]

    result = graph.invoke(state)

    assert result["attempts"] == 0
    assert result["context_next_node"] == "final"
    assert "minimal" in result["context_error"].casefold()


def test_stage4_streams_context_monitor_before_verifier(tmp_path):
    graph = build_graph(
        SequenceModel(supervisor_round()),
        SequenceModel([AIMessage(content="inspect")], [verdict(True, "good")]),
        SequenceCounter(10),
    )
    state = initial_graph_state("build", runtime=RuntimeState(tmp_path), max_attempts=1)

    chunks = list(graph.stream(state, stream_mode=["updates", "custom"]))
    custom_types = [chunk["type"] for mode, chunk in chunks if mode == "custom"]

    assert custom_types.index("context_monitor") < custom_types.index("verifier")
