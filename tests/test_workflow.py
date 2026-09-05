from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from miniclaude.core.state import RuntimeState
from miniclaude.graph.state import initial_graph_state
from miniclaude.graph.workflow import build_workflow


class SequenceModel:
    def __init__(self, replies, structured_replies=None):
        self.replies = iter(replies)
        self.structured_replies = iter(structured_replies or [])
        self.inputs = []
        self.structured_inputs = []
        self.structured_options = []
        self.tool_names = []

    def bind_tools(self, tools):
        self.tool_names.append([tool.name for tool in tools])
        return self

    def invoke(self, messages):
        self.inputs.append(list(messages))
        return next(self.replies)

    def with_structured_output(self, schema, **kwargs):
        self.structured_options.append(kwargs)
        parent = self

        class Runner:
            def invoke(self, messages):
                parent.structured_inputs.append(list(messages))
                return next(parent.structured_replies)

        return Runner()


class SequenceStructuredModel:
    def __init__(self, replies):
        self.replies = iter(replies)
        self.inputs = []

    def with_structured_output(self, schema, **kwargs):
        parent = self

        class Runner:
            def invoke(self, messages):
                parent.inputs.append(list(messages))
                return next(parent.replies)

        return Runner()


def plan(summary="Implement once"):
    return {
        "plan_summary": summary,
        "todos": [{"id": "build", "content": "Build and test"}],
        "acceptance_criteria": ["verification command passes"],
        "verification_commands": ["python -c \"print('verified')\""],
    }


def verdict(passed, reason):
    return {
        "passed": passed,
        "reason": reason,
        "checks": [
            {
                "name": "verification command passes",
                "passed": passed,
                "detail": reason,
            }
        ],
        "recommended_next_instruction": "fix it" if not passed else "",
    }


def make_graph(plans, actors, verdicts):
    return build_workflow(
        planner_model=SequenceStructuredModel(plans),
        actor_model=SequenceModel(actors),
        verifier_model=SequenceModel(
            [AIMessage(content="workspace inspected") for _ in verdicts],
            structured_replies=verdicts,
        ),
    )


def test_workflow_success_runs_each_node_once(tmp_path):
    graph = make_graph(
        [plan()],
        [AIMessage(content="implementation complete")],
        [verdict(True, "all checks pass")],
    )
    state = initial_graph_state(
        "build feature", runtime=RuntimeState(tmp_path, allow_shell=True), max_attempts=3
    )

    result = graph.invoke(state)

    assert result["passed"] is True
    assert result["attempts"] == 1
    assert result["verification_results"][0]["stdout"].strip() == "verified"
    assert "verified successfully" in result["final_answer"]
    assert {todo["status"] for todo in result["todos"]} == {"completed"}

    from miniclaude.prompts.stage2 import FINAL_PROMPT

    assert result["final_answer"] == FINAL_PROMPT["success"].format(
        attempts=1, summary="implementation complete"
    )


def test_workflow_can_verify_read_only_task_without_shell(tmp_path):
    (tmp_path / "note.txt").write_text("hello", encoding="utf-8")
    read_only_plan = {
        **plan(),
        "plan_summary": "Inspect the existing file",
        "verification_commands": [],
    }
    verifier = SequenceModel(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "FileReadTool",
                        "args": {"file_path": "note.txt"},
                        "id": "read-note",
                    }
                ],
            ),
            AIMessage(content="file inspected"),
        ],
        structured_replies=[verdict(True, "file inspected")],
    )
    graph = build_workflow(
        planner_model=SequenceStructuredModel([read_only_plan]),
        actor_model=SequenceModel([AIMessage(content="explained file")]),
        verifier_model=verifier,
    )
    state = initial_graph_state(
        "explain note", runtime=RuntimeState(tmp_path, allow_shell=False), max_attempts=1
    )

    result = graph.invoke(state)

    assert result["passed"] is True
    assert result["verification_results"] == []
    assert any(isinstance(message, SystemMessage) for message in result["messages"])
    assert any(isinstance(message, HumanMessage) for message in result["messages"])
    assert any(isinstance(message, AIMessage) for message in result["messages"])
    assert any(isinstance(message, ToolMessage) for message in result["messages"])


def test_workflow_replans_with_failure_evidence_then_succeeds(tmp_path):
    planner = SequenceStructuredModel([plan("first"), plan("revised")])
    graph = build_workflow(
        planner_model=planner,
        actor_model=SequenceModel(
            [AIMessage(content="first implementation"), AIMessage(content="fixed implementation")]
        ),
        verifier_model=SequenceModel(
            [
                AIMessage(content="first inspection"),
                AIMessage(content="second inspection"),
            ],
            structured_replies=[
                verdict(False, "missing edge case"),
                verdict(True, "edge case fixed"),
            ],
        ),
    )
    state = initial_graph_state(
        "build feature", runtime=RuntimeState(tmp_path, allow_shell=True), max_attempts=3
    )

    result = graph.invoke(state)

    assert result["passed"] is True
    assert result["attempts"] == 2
    assert result["plan_summary"] == "revised"
    assert "missing edge case" in planner.inputs[1][-1].content


def test_workflow_stops_after_max_attempts(tmp_path):
    graph = make_graph(
        [plan()],
        [AIMessage(content="implementation")],
        [verdict(False, "still failing")],
    )
    state = initial_graph_state(
        "build feature", runtime=RuntimeState(tmp_path, allow_shell=True), max_attempts=1
    )

    result = graph.invoke(state)

    assert result["passed"] is False
    assert result["attempts"] == 1
    assert "not verified" in result["final_answer"]
    assert "still failing" in result["final_answer"]
    assert {todo["status"] for todo in result["todos"]} == {"blocked"}
    assert "still failing" in result["todos"][0]["note"]


def test_workflow_rejects_passed_verdict_with_failed_check(tmp_path):
    inconsistent = {
        "passed": True,
        "reason": "mostly fine",
        "checks": [{"name": "edge case", "passed": False, "detail": "still broken"}],
        "recommended_next_instruction": "fix edge case",
    }
    graph = make_graph(
        [plan()],
        [AIMessage(content="implementation")],
        [inconsistent],
    )
    state = initial_graph_state(
        "build feature", runtime=RuntimeState(tmp_path, allow_shell=True), max_attempts=1
    )

    result = graph.invoke(state)

    assert result["passed"] is False
    assert "not verified" in result["final_answer"]


def test_workflow_rejects_verdict_missing_an_acceptance_check(tmp_path):
    two_criteria = {
        **plan(),
        "acceptance_criteria": ["verification command passes", "output is deterministic"],
    }
    graph = make_graph(
        [two_criteria],
        [AIMessage(content="implementation")],
        [verdict(True, "only one criterion checked")],
    )
    state = initial_graph_state(
        "build feature", runtime=RuntimeState(tmp_path, allow_shell=True), max_attempts=1
    )

    result = graph.invoke(state)

    assert result["passed"] is False
    assert "acceptance criteria" in result["last_error"]


def test_verifier_can_read_workspace_but_has_no_write_tools(tmp_path):
    (tmp_path / "answer.txt").write_text("workspace evidence", encoding="utf-8")
    verifier = SequenceModel(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "FileReadTool",
                        "args": {"file_path": "answer.txt"},
                        "id": "verify-read",
                    }
                ],
            ),
            AIMessage(content="inspected file and checks"),
        ],
        structured_replies=[verdict(True, "inspected file and checks pass")],
    )
    graph = build_workflow(
        planner_model=SequenceStructuredModel([plan()]),
        actor_model=SequenceModel([AIMessage(content="implementation")]),
        verifier_model=verifier,
    )
    runtime = RuntimeState(tmp_path, allow_shell=True)
    runtime.max_output_chars = 120
    state = initial_graph_state("inspect feature", runtime=runtime, max_attempts=1)

    result = graph.invoke(state)

    assert result["passed"] is True
    assert set(verifier.tool_names[0]) == {"FileReadTool", "GrepTool"}
    assert verifier.structured_options == [{"method": "function_calling"}]
    evidence = next(message for message in verifier.inputs[1] if isinstance(message, ToolMessage))
    assert "workspace evidence" in evidence.content
    verdict_context = "\n".join(str(message.content) for message in verifier.structured_inputs[0])
    assert "inspected file and checks" in verdict_context
    assert runtime.read_snapshots == {}


def test_workflow_streams_stage_events(tmp_path):
    graph = make_graph(
        [plan()],
        [AIMessage(content="implementation")],
        [verdict(True, "passed")],
    )
    state = initial_graph_state(
        "build feature", runtime=RuntimeState(tmp_path, allow_shell=True), max_attempts=1
    )

    chunks = list(graph.stream(state, stream_mode=["updates", "custom"]))

    custom = [chunk for mode, chunk in chunks if mode == "custom"]
    assert [
        event["type"]
        for event in custom
        if event["type"] in {"planner", "actor", "verifier", "final"}
    ] == [
        "planner",
        "actor",
        "verifier",
        "final",
    ]


def test_core_workflow_api_builds_and_streams_the_real_graph(tmp_path):
    from miniclaude.core.agent import stream_workflow_events

    model = SequenceModel(
        [AIMessage(content="implementation"), AIMessage(content="inspection")],
        structured_replies=[plan(), verdict(True, "passed")],
    )

    events = list(
        stream_workflow_events(
            "build feature",
            workspace=tmp_path,
            max_attempts=1,
            allow_shell=True,
            model=model,
        )
    )

    stage_events = [
        event["type"]
        for event in events
        if event["type"] in {"planner", "actor", "verifier", "final"}
    ]
    assert stage_events == ["planner", "actor", "verifier", "final"]
    assert events[-1]["passed"] is True
