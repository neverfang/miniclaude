"""Explicit opt-in acceptance test for the stage-two LangGraph workflow."""

import base64
import re

import pytest

from miniclaude.core.state import RuntimeState
from miniclaude.graph.state import initial_graph_state
from miniclaude.graph.workflow import build_workflow
from miniclaude.providers.openai_provider import create_model
from miniclaude.tools.bash_tool import run_bash

pytestmark = pytest.mark.live


def _run_semantic_harness(runtime, timeout_seconds=10):
    source = """from game_of_life import next_generation
assert next_generation({(0, 0)}) == set()
three = {(-1, 0), (0, -1), (1, 0)}
assert (0, 0) in next_generation(three | {(0, 0)})
four = three | {(0, 1)}
assert (0, 0) not in next_generation(four | {(0, 0)})
assert (0, 0) in next_generation(three)
block = {(0, 0), (0, 1), (1, 0), (1, 1)}
assert next_generation(block) == block
horizontal = {(-1, 0), (0, 0), (1, 0)}
vertical = {(0, -1), (0, 0), (0, 1)}
assert next_generation(horizontal) == vertical
assert next_generation(vertical) == horizontal
print('semantic checks passed')
"""
    encoded = base64.b64encode(source.encode("utf-8")).decode("ascii")
    command = f"python -c \"import base64;exec(base64.b64decode('{encoded}'))\""
    return run_bash(runtime, command, timeout_seconds=timeout_seconds)


def test_live_stage2_game_of_life(request, tmp_path):
    if not request.config.getoption("--run-live-stage2"):
        pytest.skip("Requires --run-live-stage2: API calls and unsandboxed generated code")
    try:
        model = create_model(env_file=request.config.rootpath / ".env")
    except ValueError as exc:
        pytest.skip(f"Live configuration not available: {exc}")

    task = (
        "Create game_of_life.py implementing Conway's Game of Life with only the Python "
        "standard library. Keep pure next_generation(cells) logic separate from its CLI. "
        "Create test_game_of_life.py with unittest cases for underpopulation, survival, "
        "overpopulation, reproduction, a still-life block, and a blinker oscillator. "
        "The command `python game_of_life.py --pattern blinker --steps 4` must print a "
        "deterministic finite text demo and exit 0 without opening a GUI. Run all tests and "
        "the demo, fixing failures. Do not install packages, use the network, or start a "
        "background process."
    )
    runtime = RuntimeState(tmp_path, allow_shell=True)
    graph = build_workflow(planner_model=model, actor_model=model, verifier_model=model)
    print(f"Generated artifacts: {tmp_path}")

    result = graph.invoke(
        initial_graph_state(task, runtime=runtime, max_attempts=3),
        config={"recursion_limit": 20},
    )

    assert result["passed"], result["final_answer"]
    assert (tmp_path / "game_of_life.py").is_file()
    assert (tmp_path / "test_game_of_life.py").is_file()
    tests = run_bash(runtime, "python -m unittest discover -v", timeout_seconds=30)
    assert tests["ok"], tests
    count = re.search(r"Ran (\d+) tests?", tests["stderr"])
    assert count and int(count.group(1)) >= 6, tests

    semantic = _run_semantic_harness(runtime)
    assert semantic["ok"], semantic
    assert semantic["stdout"].strip() == "semantic checks passed"

    command = "python game_of_life.py --pattern blinker --steps 4"
    demo = run_bash(
        runtime,
        command,
        timeout_seconds=15,
    )
    assert demo["ok"], demo
    assert demo["stdout"].strip()
    repeated = run_bash(runtime, command, timeout_seconds=15)
    assert repeated["ok"], repeated
    assert repeated["stdout"] == demo["stdout"]


def test_semantic_harness_times_out_untrusted_import(tmp_path):
    (tmp_path / "game_of_life.py").write_text("while True: pass\n", encoding="utf-8")

    result = _run_semantic_harness(RuntimeState(tmp_path, allow_shell=True), timeout_seconds=0.3)

    assert result["ok"] is False
    assert result["timed_out"] is True
