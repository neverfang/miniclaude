import json

from miniclaude.core.state import RuntimeState
from miniclaude.graph.memory import (
    RULES_LAYER,
    build_layered_memory,
    format_layered_memory_for_prompt,
)
from miniclaude.graph.state import AgentHandoff, SourceItem, TodoItem, initial_graph_state
from miniclaude.tools.history_tools import persist_history_summary
from miniclaude.tools.notepad_tools import build_notepad_tools
from miniclaude.tools.registry import execute_tool


def test_layered_memory_is_bounded_projected_and_independent(tmp_path):
    runtime = RuntimeState(tmp_path)
    persist_history_summary(runtime, "prior history")
    execute_tool(build_notepad_tools(runtime), "NotepadAppendTool", {"note": "durable decision"})
    state = initial_graph_state("build safely", runtime=runtime)
    state["plan_summary"] = "implement then verify"
    state["todos"] = [TodoItem(id="one", content="Implement", status="in_progress", note="")]
    state["research_notes"] = "r" * 3_000
    state["sources"] = [
        SourceItem(
            title=f"Source {index}",
            url=f"https://example.com/{index}",
            content="unbounded evidence",
            score=1.0,
        )
        for index in range(15)
    ]
    state["agent_handoffs"] = [
        AgentHandoff(
            from_agent="planner",
            to_agent=f"agent-{index}",
            instruction=f"instruction-{index}",
            result=f"result-{index}",
            ok=True,
        )
        for index in range(10)
    ]
    state["compression_events"] = [
        {
            "before_tokens": index + 10,
            "after_tokens": index,
            "removed_messages": 1,
            "attempt": index,
            "used_fallback": False,
        }
        for index in range(5)
    ]

    memory = build_layered_memory(state, node="context_monitor")

    assert memory["working_memory"]["node"] == "context_monitor"
    assert len(memory["working_memory"]["research_notes"]) <= 1_603
    assert len(memory["working_memory"]["sources"]) == 10
    assert set(memory["working_memory"]["sources"][0]) == {"title", "url"}
    assert len(memory["working_memory"]["agent_handoffs"]) == 6
    assert memory["working_memory"]["agent_handoffs"][0]["to_agent"] == "agent-4"
    assert memory["history_summary_store"]["history_summary"] == "prior history"
    assert memory["history_summary_store"]["notepad"] == "durable decision\n"
    assert [
        event["attempt"] for event in memory["history_summary_store"]["compression_events"]
    ] == [
        2,
        3,
        4,
    ]

    memory["rules"]["rules"].append("mutated")
    memory["working_memory"]["todos"][0]["content"] = "mutated"
    memory["working_memory"]["sources"][0]["title"] = "mutated"

    assert "mutated" not in RULES_LAYER["rules"]
    assert state["todos"][0]["content"] == "Implement"
    assert state["sources"][0]["title"] == "Source 0"


def test_layered_memory_degrades_safely_when_durable_files_are_invalid(tmp_path):
    runtime = RuntimeState(tmp_path)
    (tmp_path / "HISTORY_SUMMARY.md").write_bytes(b"\xff")
    (tmp_path / "NOTEPAD.md").write_bytes(b"\xff")
    state = initial_graph_state("recover", runtime=runtime)

    memory = build_layered_memory(state)

    store = memory["history_summary_store"]
    assert store["history_summary"] == ""
    assert store["notepad"] == ""
    assert "UTF-8" in store["history_error"]
    assert "UTF-8" in store["notepad_error"]
    assert str(tmp_path) not in json.dumps(store)


def test_layered_memory_prompt_is_stable_unicode_json(tmp_path):
    state = initial_graph_state("中文任务", runtime=RuntimeState(tmp_path))

    rendered = format_layered_memory_for_prompt(build_layered_memory(state))

    assert json.loads(rendered)["working_memory"]["task"] == "中文任务"
    assert "\\u4e2d" not in rendered
