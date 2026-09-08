from miniclaude.core.harness import HarnessRunner
from miniclaude.core.state import RuntimeState


class FakeCheckpoint:
    def __init__(self, calls, *, fail=False, snapshot_error=""):
        self.calls = calls
        self.fail = fail
        self.snapshot_error = snapshot_error

    def save(self, state, *, status="running", latest_node=None, event=None):
        if self.fail:
            raise OSError("private checkpoint path")
        self.calls.append(f"checkpoint.{status}.{latest_node}")
        return {
            "type": "checkpoint_saved",
            "status": status,
            "latest_node": latest_node,
            "snapshot_restorable": not bool(self.snapshot_error),
            "snapshot_error": self.snapshot_error,
        }


class FakeTrace:
    def __init__(self, calls, *, fail=False):
        self.calls = calls
        self.fail = fail

    def start(self, state):
        if self.fail:
            raise OSError("private trace path")
        self.calls.append("trace.start")
        return {"type": "trace_started", "trace_id": "trace-test"}

    def record_custom_event(self, event):
        if self.fail:
            raise OSError("private trace path")
        self.calls.append(f"trace.custom.{event['type']}")

    def record_graph_update(self, event):
        if self.fail:
            raise OSError("private trace path")
        self.calls.append(f"trace.node.{next(iter(event))}")

    def end(self, *, status, latest_node, final_state):
        if self.fail:
            raise OSError("private trace path")
        self.calls.append(f"trace.end.{status}")
        return {"trace_id": "trace-test", "status": status, "latest_node": latest_node}

    def pop_warning(self):
        return None


def test_harness_orders_start_updates_and_finish(tmp_path):
    calls = []
    runtime = RuntimeState(tmp_path)
    harness = HarnessRunner(
        runtime,
        task="build",
        checkpoint=FakeCheckpoint(calls),
        trace=FakeTrace(calls),
    )
    state = {"task": "build", "passed": False}

    started = harness.start(state)
    updated = harness.record_graph_update("supervisor", {"todos": []}, state)
    summary = harness.finish(status="failed", latest_node="final", state=state)

    assert [event["type"] for event in started] == ["trace_started", "checkpoint_saved"]
    assert [event["type"] for event in updated] == ["checkpoint_saved"]
    assert summary == {
        "type": "trace_summary",
        "trace_id": "trace-test",
        "status": "failed",
        "latest_node": "final",
    }
    assert calls == [
        "trace.start",
        "checkpoint.started.start",
        "trace.custom.checkpoint_saved",
        "trace.node.supervisor",
        "checkpoint.running.supervisor",
        "trace.custom.checkpoint_saved",
        "checkpoint.failed.final",
        "trace.custom.checkpoint_saved",
        "trace.end.failed",
    ]


def test_runtime_events_are_traced_checkpointed_and_drained_once(tmp_path):
    calls = []
    runtime = RuntimeState(tmp_path)
    harness = HarnessRunner(
        runtime,
        task="build",
        checkpoint=FakeCheckpoint(calls),
        trace=FakeTrace(calls),
    )
    harness.start({"task": "build"})

    runtime.event_handler({"type": "approval_requested", "approval_id": "a"})
    runtime.event_handler({"type": "approval_resolved", "approval_id": "a"})

    assert [event["type"] for event in harness.drain_runtime_events()] == [
        "approval_requested",
        "checkpoint_saved",
        "approval_resolved",
        "checkpoint_saved",
    ]
    assert harness.drain_runtime_events() == []
    assert "trace.custom.approval_requested" in calls
    assert "checkpoint.running.approval" in calls


def test_harness_storage_failures_become_sanitized_warnings(tmp_path):
    runtime = RuntimeState(tmp_path)
    harness = HarnessRunner(
        runtime,
        task="build",
        checkpoint=FakeCheckpoint([], fail=True),
        trace=FakeTrace([], fail=True),
    )

    events = harness.start({"task": "build"})

    assert {event["type"] for event in events} == {"trace_warning", "checkpoint_warning"}
    assert all("private" not in event["message"] for event in events)


def test_snapshot_failure_emits_visible_checkpoint_warning(tmp_path):
    runtime = RuntimeState(tmp_path)
    harness = HarnessRunner(
        runtime,
        task="build",
        checkpoint=FakeCheckpoint([], snapshot_error="private snapshot detail"),
        trace=FakeTrace([]),
    )

    events = harness.start({"task": "build"})

    assert [event["type"] for event in events] == [
        "trace_started",
        "checkpoint_saved",
        "checkpoint_warning",
    ]
    assert "private snapshot detail" not in events[-1]["message"]


def test_harness_finish_is_idempotent(tmp_path):
    calls = []
    harness = HarnessRunner(
        RuntimeState(tmp_path),
        task="build",
        checkpoint=FakeCheckpoint(calls),
        trace=FakeTrace(calls),
    )
    harness.start({})

    first = harness.finish(status="passed", latest_node="final", state={"passed": True})
    second = harness.finish(status="passed", latest_node="final", state={"passed": True})

    assert first["type"] == "trace_summary"
    assert second is None
    assert calls.count("trace.end.passed") == 1


def test_harness_cancel_is_idempotent(tmp_path):
    calls = []
    harness = HarnessRunner(
        RuntimeState(tmp_path),
        task="build",
        checkpoint=FakeCheckpoint(calls),
        trace=FakeTrace(calls),
    )
    harness.start({"task": "build", "passed": False})

    first = harness.cancel(latest_node="actor", reason="Escape pressed")
    second = harness.cancel(latest_node="actor", reason="again")

    assert first["type"] == "trace_summary"
    assert first["status"] == "cancelled"
    assert second is None
    assert calls.count("trace.end.cancelled") == 1
