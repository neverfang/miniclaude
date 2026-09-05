from dataclasses import dataclass

import pytest

from miniclaude.core.state import RuntimeState
from miniclaude.graph.nodes import (
    GraphNodeError,
    PlanOutput,
    make_planner_node,
    run_verification_commands,
)
from miniclaude.graph.state import VerificationResult, initial_graph_state


@dataclass
class StructuredRunner:
    reply: object
    inputs: list

    def invoke(self, messages):
        self.inputs.append(messages)
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


class StructuredModel:
    def __init__(self, reply):
        self.reply = reply
        self.inputs = []
        self.schema = None
        self.structured_options = None

    def with_structured_output(self, schema, **kwargs):
        self.schema = schema
        self.structured_options = kwargs
        return StructuredRunner(self.reply, self.inputs)


def valid_plan():
    return {
        "plan_summary": "Test first, then implement.",
        "todos": [
            {"id": "tests", "content": "Write tests"},
            {"id": "code", "content": "Implement behavior"},
        ],
        "acceptance_criteria": ["All tests pass"],
        "verification_commands": ["python -m unittest -v"],
    }


def test_planner_returns_validated_plan_and_todos(tmp_path):
    model = StructuredModel(valid_plan())
    state = initial_graph_state("build feature", runtime=RuntimeState(tmp_path))

    update = make_planner_node(model)(state)

    assert model.schema is PlanOutput
    assert model.structured_options == {"method": "function_calling"}
    assert update["plan_summary"] == "Test first, then implement."
    assert [todo["status"] for todo in update["todos"]] == ["in_progress", "pending"]
    assert update["acceptance_criteria"] == ["All tests pass"]
    assert update["verification_commands"] == ["python -m unittest -v"]


def test_planner_publishes_todos_through_the_tool_boundary(tmp_path, monkeypatch):
    import miniclaude.graph.nodes as nodes
    from miniclaude.tools.registry import execute_tool as real_execute_tool

    calls = []

    def spy(tools, name, args):
        calls.append((tools, name, args))
        return real_execute_tool(tools, name, args)

    monkeypatch.setattr(nodes, "execute_tool", spy)
    state = initial_graph_state("build feature", runtime=RuntimeState(tmp_path))

    update = make_planner_node(StructuredModel(valid_plan()))(state)

    assert calls[0][1] == "TodoWriteTool"
    assert calls[0][2]["todos"] == update["todos"]


def test_planner_receives_previous_failure_when_replanning(tmp_path):
    model = StructuredModel(valid_plan())
    state = initial_graph_state("repair feature", runtime=RuntimeState(tmp_path))
    state["attempts"] = 1
    state["last_error"] = "unit test failed"
    state["verification_results"] = [
        VerificationResult(
            command="python -m unittest -v",
            ok=False,
            exit_code=1,
            stdout="",
            stderr="AssertionError: expected 2",
        )
    ]

    make_planner_node(model)(state)

    prompt = model.inputs[0][-1].content
    assert "unit test failed" in prompt
    assert "AssertionError" in prompt
    assert "attempt 2" in prompt


@pytest.mark.parametrize(
    "reply",
    [
        {},
        {**valid_plan(), "todos": []},
        {**valid_plan(), "verification_commands": ["  "]},
        {**valid_plan(), "acceptance_criteria": ["  "]},
        {**valid_plan(), "acceptance_criteria": ["same", "same"]},
        Exception("Authorization: secret-value"),
    ],
)
def test_planner_invalid_output_fails_without_secret_details(tmp_path, reply):
    state = initial_graph_state("task", runtime=RuntimeState(tmp_path))

    with pytest.raises(GraphNodeError) as caught:
        make_planner_node(StructuredModel(reply))(state)

    assert "secret-value" not in str(caught.value)
    assert "Planner" in str(caught.value)


def test_verification_commands_capture_success_and_failure(tmp_path):
    runtime = RuntimeState(tmp_path, allow_shell=True)

    results = run_verification_commands(
        runtime,
        [
            'python -c "print(2 + 3)"',
            "python -c \"import sys; print('bad', file=sys.stderr); sys.exit(4)\"",
        ],
    )

    assert results[0]["ok"] is True
    assert results[0]["exit_code"] == 0
    assert results[0]["stdout"].strip() == "5"
    assert results[1]["ok"] is False
    assert results[1]["exit_code"] == 4
    assert results[1]["stderr"].strip() == "bad"


def test_verification_commands_respect_shell_opt_in(tmp_path):
    runtime = RuntimeState(tmp_path, allow_shell=False)

    results = run_verification_commands(runtime, ["python --version"])

    assert results == [
        VerificationResult(
            command="python --version",
            ok=False,
            exit_code=None,
            stdout="",
            stderr="Shell execution is disabled; rerun with --allow-shell",
        )
    ]


def test_verification_commands_validate_count_and_content(tmp_path):
    runtime = RuntimeState(tmp_path, allow_shell=True)
    assert run_verification_commands(runtime, []) == []
    with pytest.raises(ValueError, match="at most"):
        run_verification_commands(runtime, ["echo ok"] * 11)
    with pytest.raises(ValueError, match="empty"):
        run_verification_commands(runtime, ["  "])


def test_verification_process_failure_becomes_safe_evidence(tmp_path, monkeypatch):
    import miniclaude.graph.nodes as nodes

    def fail_to_start(*args, **kwargs):
        raise OSError("secret path detail")

    monkeypatch.setattr(nodes, "run_bash", fail_to_start)

    results = run_verification_commands(RuntimeState(tmp_path, allow_shell=True), ["test command"])

    assert results[0]["ok"] is False
    assert results[0]["exit_code"] is None
    assert "OSError" in results[0]["stderr"]
    assert "secret path detail" not in results[0]["stderr"]
