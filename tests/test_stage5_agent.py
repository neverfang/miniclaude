from miniclaude.core.agent import stream_workflow_events


class PassingWorkflow:
    def stream(self, state, **kwargs):
        yield (
            "custom",
            {"type": "context_monitor", "tokens": 10, "limit": 400_000, "route": "verifier"},
        )
        yield (
            "updates",
            {
                "contextual_supervisor": {
                    "plan_summary": "build",
                    "todos": [],
                    "acceptance_criteria": ["done"],
                    "verification_commands": [],
                    "last_error": "",
                }
            },
        )
        yield (
            "updates",
            {
                "verifier": {
                    "attempts": 1,
                    "passed": True,
                    "verification_reason": "done",
                    "verification_checks": [],
                    "verification_results": [],
                }
            },
        )
        yield "updates", {"final": {"final_answer": "verified"}}


def test_stage_five_stream_wraps_stage_four_with_harness_events(tmp_path):
    events = list(
        stream_workflow_events(
            "build",
            workspace=tmp_path,
            model=object(),
            workflow=PassingWorkflow(),
            checkpoint_mode="light",
            trace_mode="on",
        )
    )
    kinds = [event["type"] for event in events]

    assert kinds[0:3] == ["run_start", "trace_started", "checkpoint_saved"]
    assert "context_monitor" in kinds
    assert "supervisor" in kinds
    assert "verifier" in kinds
    assert "final" in kinds
    assert kinds[-2:] == ["checkpoint_saved", "trace_summary"]
    assert events[-1]["status"] == "passed"
    assert (tmp_path / ".miniclaude/checkpoints/checkpoint.json").exists()
    assert len(list((tmp_path / ".miniclaude/traces").iterdir())) == 1


def test_stage_five_stream_passes_runtime_policies_to_tools(tmp_path):
    captured = {}

    class CapturingWorkflow:
        def stream(self, state, **kwargs):
            captured["runtime"] = state["runtime"]
            yield "updates", {"final": {"final_answer": "failed"}}

    list(
        stream_workflow_events(
            "build",
            workspace=tmp_path,
            model=object(),
            workflow=CapturingWorkflow(),
            approval_mode="deny",
            checkpoint_mode="off",
            trace_mode="off",
        )
    )

    assert captured["runtime"].approval_mode == "deny"
    assert captured["runtime"].checkpoint_mode == "off"
    assert captured["runtime"].trace_mode == "off"


def test_stage_five_interrupt_finalizes_harness(tmp_path):
    class InterruptingWorkflow:
        def stream(self, state, **kwargs):
            yield "updates", {"contextual_supervisor": {"plan_summary": "started"}}
            raise KeyboardInterrupt

    iterator = stream_workflow_events(
        "build",
        workspace=tmp_path,
        model=object(),
        workflow=InterruptingWorkflow(),
        checkpoint_mode="strict",
        trace_mode="on",
    )

    try:
        list(iterator)
    except KeyboardInterrupt:
        pass
    else:
        raise AssertionError("KeyboardInterrupt was not propagated")

    checkpoint = (tmp_path / ".miniclaude/checkpoints/checkpoint.json").read_text(encoding="utf-8")
    assert '"status": "interrupted"' in checkpoint
    trace_files = list((tmp_path / ".miniclaude/traces").glob("*/trace.json"))
    assert len(trace_files) == 1
    assert '"status": "interrupted"' in trace_files[0].read_text(encoding="utf-8")


def test_stage_five_checkpoints_merge_reducer_sensitive_messages(tmp_path):
    import json

    from langchain_core.messages import AIMessage, HumanMessage

    class MessageWorkflow:
        def stream(self, state, **kwargs):
            yield (
                "updates",
                {
                    "contextual_supervisor": {
                        "messages": [HumanMessage(content="first")],
                        "plan_summary": "continue",
                    }
                },
            )
            yield (
                "updates",
                {
                    "verifier": {
                        "messages": [AIMessage(content="second")],
                        "attempts": 1,
                        "passed": True,
                        "verification_reason": "done",
                        "verification_checks": [],
                        "verification_results": [],
                    }
                },
            )
            yield "updates", {"final": {"final_answer": "verified"}}

    list(
        stream_workflow_events(
            "build",
            workspace=tmp_path,
            model=object(),
            workflow=MessageWorkflow(),
            checkpoint_mode="light",
            trace_mode="off",
        )
    )

    payload = json.loads(
        (tmp_path / ".miniclaude/checkpoints/checkpoint.json").read_text(encoding="utf-8")
    )
    assert [message["type"] for message in payload["state"]["messages"]] == [
        "human",
        "ai",
    ]


def test_stage_five_trace_counts_nested_specialist_tool_events(tmp_path):
    class ToolWorkflow:
        def stream(self, state, **kwargs):
            yield (
                "custom",
                {
                    "type": "code_agent_event",
                    "event": {"type": "tool_call", "name": "BashTool", "args": {}},
                },
            )
            yield (
                "custom",
                {
                    "type": "code_agent_event",
                    "event": {
                        "type": "tool_result",
                        "name": "BashTool",
                        "result": {"ok": False},
                    },
                },
            )
            yield "updates", {"final": {"final_answer": "not verified"}}

    events = list(
        stream_workflow_events(
            "build",
            workspace=tmp_path,
            model=object(),
            workflow=ToolWorkflow(),
            checkpoint_mode="off",
            trace_mode="on",
        )
    )

    summary = next(event for event in events if event["type"] == "trace_summary")
    assert summary["tool_calls"] == 1
    assert summary["failed_tool_calls"] == 1
