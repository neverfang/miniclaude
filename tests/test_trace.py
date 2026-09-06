import json
from datetime import UTC, datetime, timedelta

from miniclaude.core.state import RuntimeState
from miniclaude.core.trace import TraceRecorder


class FakeTime:
    def __init__(self):
        self.started = datetime(2026, 9, 6, 1, 2, 3, tzinfo=UTC)
        self.elapsed = 100.0

    def now(self):
        value = self.started
        self.started += timedelta(seconds=1)
        return value

    def monotonic(self):
        value = self.elapsed
        self.elapsed += 0.25
        return value


def _recorder(tmp_path, *, mode="on", trace_id="trace-fixed", resumed_from=None):
    fake = FakeTime()
    runtime = RuntimeState(tmp_path, trace_mode=mode, trace_id=trace_id)
    return TraceRecorder(
        runtime,
        task="build app",
        now=fake.now,
        clock=fake.monotonic,
        resumed_from_trace_id=resumed_from,
    )


def test_trace_records_order_stats_and_summary(tmp_path):
    recorder = _recorder(tmp_path)

    started = recorder.start({"task": "build app"})
    recorder.record_custom_event({"type": "handoff", "to_agent": "codeAgent"})
    recorder.record_custom_event({"type": "tool_call", "name": "BashTool"})
    recorder.record_custom_event(
        {"type": "tool_result", "name": "BashTool", "result": {"ok": False}}
    )
    summary = recorder.end(
        status="failed",
        latest_node="verifier",
        final_state={"passed": False},
    )

    events = [
        json.loads(line) for line in recorder.events_path.read_text(encoding="utf-8").splitlines()
    ]
    assert started == {"type": "trace_started", "trace_id": "trace-fixed"}
    assert [event["sequence"] for event in events] == list(range(1, len(events) + 1))
    assert [event["type"] for event in events] == [
        "run_start",
        "handoff",
        "tool_call",
        "tool_result",
        "run_end",
    ]
    assert summary["tool_calls"] == 1
    assert summary["failed_tool_calls"] == 1
    assert summary["handoff_count"] == 1
    assert summary["status"] == "failed"
    assert summary["latest_node"] == "verifier"
    assert (recorder.root / "trace.json").exists()
    assert (recorder.root / "timeline.md").exists()


def test_trace_counts_graph_and_harness_events(tmp_path):
    recorder = _recorder(tmp_path)
    recorder.start({})

    recorder.record_graph_update({"supervisor": {"todos": []}})
    recorder.record_graph_update({"verifier": {"passed": False}})
    recorder.record_custom_event({"type": "approval_resolved", "approved": True})
    recorder.record_custom_event({"type": "checkpoint_saved"})
    recorder.record_custom_event({"type": "context_compressor"})
    summary = recorder.end(status="passed", latest_node="final", final_state={"passed": True})

    assert summary["node_visits"] == {"supervisor": 1, "verifier": 1}
    assert summary["approval_count"] == 1
    assert summary["checkpoint_count"] == 1
    assert summary["compression_count"] == 1


def test_trace_redacts_secrets_and_bounds_large_values(tmp_path):
    recorder = _recorder(tmp_path)
    recorder.start({})
    recorder.record_custom_event(
        {
            "type": "tool_call",
            "api_key": "sk-do-not-store",
            "command": "tool --token token-value",
            "output": "x" * 20_000,
        }
    )
    recorder.end(status="failed", latest_node="tool", final_state={})

    persisted = recorder.events_path.read_text(encoding="utf-8")
    assert "sk-do-not-store" not in persisted
    assert "token-value" not in persisted
    assert (
        len(
            max(
                json.loads(line).get("data", {}).get("output", "")
                for line in persisted.splitlines()
            )
        )
        <= 8_000
    )


def test_trace_off_mode_writes_nothing(tmp_path):
    recorder = _recorder(tmp_path, mode="off")

    assert recorder.start({}) is None
    recorder.record_custom_event({"type": "tool_call"})
    assert recorder.end(status="passed", latest_node="final", final_state={}) is None
    assert not recorder.root.exists()


def test_trace_write_failure_is_nonfatal_and_warning_is_reported_once(tmp_path, monkeypatch):
    recorder = _recorder(tmp_path)

    def fail_write(record):
        raise OSError("private path detail")

    monkeypatch.setattr(recorder, "_write_event", fail_write)

    assert recorder.start({}) == {"type": "trace_started", "trace_id": "trace-fixed"}
    recorder.record_custom_event({"type": "tool_call"})
    warning = recorder.pop_warning()

    assert warning == {"type": "trace_warning", "message": "Trace persistence failed (OSError)"}
    assert recorder.pop_warning() is None


def test_resumed_trace_links_history_and_does_not_reuse_existing_directory(tmp_path):
    first = _recorder(tmp_path, trace_id="trace-fixed")
    first.start({})
    first.end(status="interrupted", latest_node="supervisor", final_state={})
    original = first.events_path.read_text(encoding="utf-8")

    second = _recorder(tmp_path, trace_id="trace-fixed", resumed_from="trace-fixed")
    second.start({})
    summary = second.end(status="passed", latest_node="final", final_state={})

    assert second.trace_id != first.trace_id
    assert summary["resumed_from_trace_id"] == "trace-fixed"
    assert first.events_path.read_text(encoding="utf-8") == original
