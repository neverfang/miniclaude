import json

import pytest

from miniclaude.core.agent import stream_workflow_events


class InterruptingWorkflow:
    def __init__(self, workspace):
        self.workspace = workspace

    def stream(self, state, **kwargs):
        (self.workspace / "app.py").write_text("checkpoint v1", encoding="utf-8")
        yield (
            "custom",
            {
                "type": "approval_requested",
                "approval_id": "approval-test",
                "risk_level": "risky",
                "risk_reason": "Network download command",
                "command": "curl https://example.test",
            },
        )
        yield (
            "updates",
            {
                "contextual_supervisor": {
                    "plan_summary": "continue after interruption",
                    "todos": [
                        {
                            "id": "code",
                            "content": "Finish app",
                            "status": "in_progress",
                            "note": "checkpointed",
                        }
                    ],
                    "last_error": "",
                }
            },
        )
        raise KeyboardInterrupt


class PassingResumeWorkflow:
    def __init__(self):
        self.initial_state = None

    def stream(self, state, **kwargs):
        self.initial_state = dict(state)
        yield (
            "updates",
            {
                "verifier": {
                    "attempts": state.get("attempts", 0) + 1,
                    "passed": True,
                    "verification_reason": "done",
                    "verification_checks": [
                        {"name": "app exists", "passed": True, "detail": "checked"}
                    ],
                    "verification_results": [],
                }
            },
        )
        yield "updates", {"final": {"final_answer": "verified"}}


def _interrupt(workspace):
    with pytest.raises(KeyboardInterrupt):
        list(
            stream_workflow_events(
                "build app",
                workspace=workspace,
                model=object(),
                workflow=InterruptingWorkflow(workspace),
                approval_mode="deny",
                checkpoint_mode="strict",
                trace_mode="on",
            )
        )
    return json.loads(
        (workspace / ".miniclaude/checkpoints/checkpoint.json").read_text(
            encoding="utf-8"
        )
    )


def test_stage_five_interrupt_resume_and_trace(tmp_path):
    checkpoint = _interrupt(tmp_path)
    assert checkpoint["status"] == "interrupted"
    assert checkpoint["latest_node"] == "contextual_supervisor"
    first_trace_id = checkpoint["trace_id"]

    target = tmp_path / "app.py"
    target.write_text("manual v2", encoding="utf-8")
    resumed = PassingResumeWorkflow()
    events = list(
        stream_workflow_events(
            "build app",
            workspace=tmp_path,
            resume_workspace=tmp_path,
            model=object(),
            workflow=resumed,
            approval_mode="deny",
            checkpoint_mode="strict",
            trace_mode="on",
        )
    )

    assert any(event["type"] == "resume_loaded" for event in events)
    assert next(event for event in events if event["type"] == "final")["passed"] is True
    assert resumed.initial_state["plan_summary"] == "continue after interruption"
    assert resumed.initial_state["passed"] is False
    assert target.read_text(encoding="utf-8") == "manual v2"
    assert len(list((tmp_path / ".miniclaude/traces").iterdir())) == 2
    summary = next(event for event in events if event["type"] == "trace_summary")
    assert summary["resumed_from_trace_id"] == first_trace_id

    trace_events = [
        json.loads(line)
        for line in (
            tmp_path / ".miniclaude/traces" / summary["trace_id"] / "events.jsonl"
        )
        .read_text(encoding="utf-8")
        .splitlines()
    ]
    assert [event["type"] for event in trace_events[-2:]] == [
        "checkpoint_saved",
        "run_end",
    ]


def test_explicit_resume_restore_uses_checkpoint_and_preserves_untracked(tmp_path):
    _interrupt(tmp_path)
    target = tmp_path / "app.py"
    target.write_text("manual v2", encoding="utf-8")
    untracked = tmp_path / "notes.local"
    untracked.write_text("keep me", encoding="utf-8")

    events = list(
        stream_workflow_events(
            "build app",
            workspace=tmp_path,
            resume_workspace=tmp_path,
            restore_workspace=True,
            model=object(),
            workflow=PassingResumeWorkflow(),
            approval_mode="deny",
            checkpoint_mode="strict",
            trace_mode="on",
        )
    )

    resume = next(event for event in events if event["type"] == "resume_loaded")
    assert resume["restore"]["type"] == "restore_completed"
    assert target.read_text(encoding="utf-8") == "checkpoint v1"
    assert untracked.read_text(encoding="utf-8") == "keep me"
