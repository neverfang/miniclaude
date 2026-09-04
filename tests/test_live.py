"""Explicit opt-in: these tests call an API and execute model-generated code."""

import re

import pytest

from miniclaude.core.agent import stream_agent_events
from miniclaude.core.state import RuntimeState
from miniclaude.providers.openai_provider import create_model
from miniclaude.tools.bash_tool import run_bash

pytestmark = pytest.mark.live


def _run(request, tmp_path, option, task):
    if not request.config.getoption(option):
        pytest.skip(f"Requires explicit {option}: API calls and unsandboxed generated code")
    try:
        model = create_model(env_file=request.config.rootpath / ".env")
    except ValueError as exc:
        pytest.skip(f"Live configuration not available: {exc}")
    print(f"Generated artifacts: {tmp_path}")
    events = list(
        stream_agent_events(task, workspace=tmp_path, allow_shell=True, model=model, max_loops=20)
    )
    assert events[-1]["type"] == "final_answer", events[-1]
    calls = [event for event in events if event["type"] == "tool_result"]
    assert any(e["name"] == "FileWriteTool" and e["result"]["ok"] for e in calls)
    assert any(e["name"] == "BashTool" and e["result"]["ok"] for e in calls)
    state = RuntimeState(tmp_path, allow_shell=True)
    verification = run_bash(state, "python -m unittest discover -v", timeout_seconds=30)
    assert verification["ok"], verification
    assert re.search(r"Ran [1-9]\d* tests?", verification["stderr"]), verification
    return state


def test_live_add(request, tmp_path):
    state = _run(
        request,
        tmp_path,
        "--run-live",
        "Create add.py with add(a, b) returning their sum. Create test_add.py using unittest "
        "covering positive, negative and zero cases. Use only Python standard library. "
        "Run python -m unittest discover -v and fix failures. Do not install dependencies.",
    )
    assert (tmp_path / "add.py").is_file()
    result = run_bash(
        state,
        'python -c "from add import add; assert add(2,3)==5; '
        'assert add(-2,2)==0; assert add(0,0)==0"',
    )
    assert result["ok"], result


def test_live_snake(request, tmp_path):
    state = _run(
        request,
        tmp_path,
        "--run-live-snake",
        "Create a small snake game in snake.py using only the standard library. "
        "Separate game logic from tkinter UI. Normal CLI launch may open the GUI, but "
        "--headless --steps 5 must run five deterministic simulation steps without opening "
        "a window, print a summary and exit 0. Create test_snake.py with unittest cases for "
        "movement, food/growth and collisions. Run unittest and the headless demo, fix errors. "
        "Do NOT open a GUI, install dependencies, or start a background process.",
    )
    assert (tmp_path / "snake.py").is_file()
    result = run_bash(state, "python snake.py --headless --steps 5", timeout_seconds=15)
    assert result["ok"], result
    assert result["stdout"].strip()
