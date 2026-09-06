"""Explicit opt-in paid acceptance for the Stage 5 execution harness."""

import pytest
from langchain_core.tools import StructuredTool

from miniclaude.core.agent import stream_workflow_events
from miniclaude.core.approval import CommandRisk, classify_command_risk
from miniclaude.graph.stage4_workflow import build_stage4_workflow
from miniclaude.providers.openai_provider import create_model

pytestmark = pytest.mark.live


def test_live_stage5_harness_acceptance(request, tmp_path, monkeypatch):
    if not request.config.getoption("--run-live-stage5"):
        pytest.skip("Requires --run-live-stage5: paid DeepSeek calls and generated execution")
    try:
        model = create_model(env_file=request.config.rootpath / ".env")
    except ValueError as exc:
        pytest.skip(f"Live configuration not available: {exc}")

    def no_search(query: str) -> dict:
        """Keep the Stage 5 acceptance independent from Tavily and external research."""
        return {"ok": False, "error": "Web research is disabled for this acceptance test"}

    def controlled_classifier(command: str) -> CommandRisk:
        risk = classify_command_risk(command)
        if risk.level == "safe" and "unittest" in command.lower():
            return CommandRisk("risky", "Injected acceptance-test approval", command)
        return risk

    monkeypatch.setattr("miniclaude.tools.bash_tool.classify_command_risk", controlled_classifier)
    workflow = build_stage4_workflow(
        supervisor_model=model,
        verifier_model=model,
        web_search_tool=StructuredTool.from_function(no_search, name="WebSearchTool"),
        context_counter=model,
        supervisor_max_loops=20,
        verifier_max_loops=12,
    )
    task = (
        "Create calculator.py using only the Python standard library with documented add and "
        "divide functions; divide by zero must raise ValueError. Create test_calculator.py with "
        "unittest coverage, then run python -m unittest discover -v and repair any failures. "
        "Do not search the web, install packages, download files, start a server, or open a GUI."
    )

    events = list(
        stream_workflow_events(
            task,
            workspace=tmp_path,
            model=model,
            workflow=workflow,
            allow_shell=True,
            approval_mode="auto",
            checkpoint_mode="strict",
            trace_mode="on",
            max_loops=20,
            max_attempts=3,
        )
    )

    final = next(event for event in events if event["type"] == "final")
    assert final["passed"], final["content"]
    assert any(event["type"] == "approval_resolved" and event.get("approved") for event in events)
    assert (tmp_path / "calculator.py").is_file()
    assert (tmp_path / "test_calculator.py").is_file()
    assert (tmp_path / ".miniclaude/checkpoints/state.json").is_file()
    assert len(list((tmp_path / ".miniclaude/traces").glob("*/trace.json"))) == 1
