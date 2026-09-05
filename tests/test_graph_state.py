import pytest
from langgraph.graph.message import add_messages

from miniclaude.core.state import RuntimeState, ToolError
from miniclaude.graph.state import TodoItem, initial_graph_state
from miniclaude.tools.registry import execute_tool
from miniclaude.tools.todo_tools import TodoTracker, build_todo_tools


def test_graph_messages_use_langgraph_message_reducer():
    from typing import get_type_hints

    from miniclaude.graph.state import MiniclaudeGraphState

    annotation = get_type_hints(MiniclaudeGraphState, include_extras=True)["messages"]

    assert annotation.__metadata__ == (add_messages,)


def test_graph_state_allows_partial_node_updates():
    from miniclaude.graph.state import MiniclaudeGraphState

    assert MiniclaudeGraphState.__total__ is False


def test_initial_graph_state_has_bounded_retry_defaults(tmp_path):
    runtime = RuntimeState(tmp_path / "workspace", allow_shell=True)

    state = initial_graph_state("build a game", runtime=runtime, max_attempts=2)

    assert state["task"] == "build a game"
    assert state["runtime"] is runtime
    assert state["attempts"] == 0
    assert state["max_attempts"] == 2
    assert state["todos"] == []
    assert state["verification_results"] == []
    assert state["passed"] is False


@pytest.mark.parametrize("task,max_attempts", [("", 3), ("task", 0), ("task", 11)])
def test_initial_graph_state_rejects_invalid_inputs(tmp_path, task, max_attempts):
    with pytest.raises(ValueError):
        initial_graph_state(
            task, runtime=RuntimeState(tmp_path / "workspace"), max_attempts=max_attempts
        )


def test_todo_write_publishes_valid_unique_plan():
    tracker = TodoTracker()
    tools = build_todo_tools(tracker)

    result = execute_tool(
        tools,
        "TodoWriteTool",
        {
            "todos": [
                {"id": "tests", "content": "Write tests", "status": "in_progress"},
                {"id": "code", "content": "Implement feature", "status": "pending"},
            ]
        },
    )

    assert result == {"ok": True, "count": 2}
    assert tracker.snapshot() == [
        TodoItem(id="tests", content="Write tests", status="in_progress", note=""),
        TodoItem(id="code", content="Implement feature", status="pending", note=""),
    ]


def test_todo_update_changes_only_named_item():
    tracker = TodoTracker(
        [
            TodoItem(id="tests", content="Write tests", status="in_progress", note=""),
            TodoItem(id="code", content="Implement", status="pending", note=""),
        ]
    )

    result = execute_tool(
        build_todo_tools(tracker),
        "TodoUpdateTool",
        {"todo_id": "tests", "status": "completed", "note": "3 tests pass"},
    )

    assert result["ok"] is True
    assert tracker.snapshot()[0]["status"] == "completed"
    assert tracker.snapshot()[0]["note"] == "3 tests pass"
    assert tracker.snapshot()[1]["status"] == "pending"


@pytest.mark.parametrize(
    "todos,error",
    [
        ([], "at least one"),
        ([{"id": "x", "content": "one"}, {"id": "x", "content": "two"}], "unique"),
        ([{"id": "x", "content": "  "}], "content"),
    ],
)
def test_todo_write_rejects_invalid_plan_without_mutation(todos, error):
    original = [TodoItem(id="safe", content="Keep me", status="pending", note="")]
    tracker = TodoTracker(original)

    result = execute_tool(build_todo_tools(tracker), "TodoWriteTool", {"todos": todos})

    assert result["ok"] is False
    assert error in result["error"]
    assert tracker.snapshot() == original


def test_todo_update_rejects_unknown_id_without_mutation():
    original = [TodoItem(id="safe", content="Keep me", status="pending", note="")]
    tracker = TodoTracker(original)

    result = execute_tool(
        build_todo_tools(tracker),
        "TodoUpdateTool",
        {"todo_id": "missing", "status": "blocked"},
    )

    assert result["ok"] is False
    assert "Unknown todo" in result["error"]
    assert tracker.snapshot() == original


def test_todo_tracker_defensively_copies_inputs_and_snapshots():
    source = [TodoItem(id="one", content="First", status="pending", note="")]
    tracker = TodoTracker(source)
    source[0]["content"] = "changed outside"
    snapshot = tracker.snapshot()
    snapshot[0]["content"] = "changed snapshot"

    assert tracker.snapshot()[0]["content"] == "First"


def test_todo_tracker_rejects_invalid_status():
    tracker = TodoTracker()
    with pytest.raises(ToolError, match="status"):
        tracker.write([{"id": "one", "content": "First", "status": "doing"}])
