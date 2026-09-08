from pathlib import Path

from miniclaude.cli.tui.state import (
    initial_session_view,
    reduce_session_event,
)


def test_sidebar_reducer_tracks_workflow_metrics():
    state = initial_session_view("abc123def456", Path("workspace"))
    for event in [
        {"type": "session_status", "status": "running"},
        {"type": "intent_decision", "route": "workflow"},
        {
            "type": "react_event",
            "event": {"type": "tool_result", "result": {"ok": False}},
        },
        {"type": "approval_requested"},
        {"type": "checkpoint_saved", "checkpoint_id": "cp-1"},
    ]:
        state = reduce_session_event(state, event)

    assert state.status == "waiting approval"
    assert state.route == "workflow"
    assert state.tool_calls == 1
    assert state.failed_tools == 1
    assert state.approvals == 1
    assert state.approval_pending is True
    assert state.checkpoint == "cp-1"


def test_sidebar_reducer_completes_and_clears_pending_approval():
    state = initial_session_view("abc123def456", Path("workspace"))
    state = reduce_session_event(state, {"type": "approval_requested"})
    state = reduce_session_event(
        state,
        {"type": "approval_resolved", "approved": False},
    )
    state = reduce_session_event(
        state,
        {"type": "session_final", "route": "workflow", "passed": True},
    )

    assert state.status == "completed"
    assert state.approval_pending is False
    assert state.turns == 1


def test_sidebar_reducer_tracks_runtime_policy():
    state = initial_session_view(
        "abc123def456",
        Path("workspace"),
        shell_enabled=False,
        approval_mode="inline",
    )

    state = reduce_session_event(
        state,
        {"type": "runtime_policy", "shell_enabled": True, "approval_mode": "all"},
    )

    assert state.shell_enabled is True
    assert state.approval_mode == "all"


def test_cancellation_moves_through_cancelling_to_idle():
    state = initial_session_view("abc123def456", Path("workspace"))
    state = reduce_session_event(state, {"type": "session_status", "status": "running"})
    state = reduce_session_event(state, {"type": "session_cancelling"})
    assert state.status == "cancelling"

    state = reduce_session_event(state, {"type": "session_cancelled"})
    assert state.status == "idle"
    assert state.approval_pending is False
