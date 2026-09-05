"""Explicit opt-in end-to-end acceptance for Stage 4."""

import json

import pytest
from langchain_core.tools import StructuredTool

from miniclaude.core.state import RuntimeState
from miniclaude.graph.stage4_workflow import build_stage4_workflow
from miniclaude.graph.state import initial_graph_state
from miniclaude.providers.openai_provider import create_model
from miniclaude.tools.bash_tool import run_bash

pytestmark = pytest.mark.live


class ForceOneCompression:
    def __init__(self):
        self.calls = 0

    def get_num_tokens_from_messages(self, messages):
        self.calls += 1
        return 1_001 if self.calls == 1 else 100


def test_live_stage4_context_recovery(request, tmp_path):
    if not request.config.getoption("--run-live-stage4"):
        pytest.skip("Requires --run-live-stage4: paid DeepSeek calls and generated code")
    try:
        model = create_model(env_file=request.config.rootpath / ".env")
    except ValueError as exc:
        pytest.skip(f"Live configuration not available: {exc}")

    def no_search(query: str) -> dict:
        """Reject network research in this standard-library-only acceptance task."""
        return {"ok": False, "error": "Web research is disabled for this acceptance test"}

    task = (
        "Create calculator.py with documented add, subtract, multiply, and divide functions "
        "using only the Python standard library. Division by zero must raise ValueError. "
        "Create test_calculator.py with unittest coverage for every operation and the error. "
        "Record a short durable implementation decision in NOTEPAD.md. Run "
        "python -m unittest discover -v, repair failures, and verify the real files. "
        "Do not search the web, install packages, start a server, or open a GUI."
    )
    runtime = RuntimeState(tmp_path, allow_shell=True)
    counter = ForceOneCompression()
    graph = build_stage4_workflow(
        supervisor_model=model,
        verifier_model=model,
        web_search_tool=StructuredTool.from_function(no_search, name="WebSearchTool"),
        context_counter=counter,
        supervisor_max_loops=20,
        verifier_max_loops=12,
    )
    state = initial_graph_state(task, runtime=runtime, max_attempts=3)
    state["context_token_limit"] = 1_000

    result = graph.invoke(state, config={"recursion_limit": 60})

    assert result["passed"], result["final_answer"]
    assert len(result["compression_events"]) >= 1
    assert result["context_token_count"] < 10_000
    for name in (
        "HISTORY_SUMMARY.md",
        "NOTEPAD.md",
        "calculator.py",
        "test_calculator.py",
    ):
        assert (tmp_path / name).is_file(), name
    tests = run_bash(runtime, "python -m unittest discover -v", timeout_seconds=30)
    assert tests["ok"], tests
    persisted = (tmp_path / "HISTORY_SUMMARY.md").read_text(encoding="utf-8")
    captured = json.dumps(
        {
            "events": result["compression_events"],
            "summary": result["context_summary"],
            "history": persisted,
        },
        ensure_ascii=False,
    )
    assert "API_KEY" not in captured
    assert "Bearer " not in captured
