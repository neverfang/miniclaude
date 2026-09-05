"""Implementation-only specialist for stage three."""

import json
from collections.abc import Callable

from miniclaude.core.agent import ChatModel, stream_agent_events
from miniclaude.graph.memory import prompt_memory_fields
from miniclaude.graph.state import MiniclaudeGraphState
from miniclaude.prompts.stage3 import CODE_AGENT_PROMPT
from miniclaude.tools.notepad_tools import build_notepad_tools
from miniclaude.tools.registry import build_tools
from miniclaude.tools.todo_tools import TodoTracker, build_todo_update_tool


def run_code_agent(
    state: MiniclaudeGraphState,
    instruction: str,
    *,
    model: ChatModel,
    writer: Callable[[dict], None] | None = None,
    max_loops: int = 10,
) -> dict:
    """Run implementation tools with research and Todo context."""
    if not state.get("todos"):
        return {
            "ok": False,
            "summary": "CodeAgent requires a published Todo plan",
            "todos": [],
            "messages": [],
            "tool_events": [],
        }
    tracker = TodoTracker(state.get("todos", []))
    tools = build_tools(state["runtime"])
    tools.append(build_todo_update_tool(tracker))
    tools.extend(build_notepad_tools(state["runtime"]))
    captured_messages = []
    tool_events = []
    summary = ""
    error = ""
    todo_updated = False
    task_data = {
        "user_task": state["task"],
        "instruction": instruction,
        "todos": tracker.snapshot(),
        "research_notes": state.get("research_notes", "")[:4000],
        "sources": state.get("sources", [])[:10],
        "previous_failure": state.get("last_error", "")[:2000],
    }
    task_data.update(prompt_memory_fields(state))
    task = json.dumps(task_data, ensure_ascii=False)
    for event in stream_agent_events(
        task,
        workspace=state["runtime"].workspace,
        runtime=state["runtime"],
        model=model,
        tools=tools,
        system_prompt=CODE_AGENT_PROMPT,
        max_loops=max_loops,
        captured_messages=captured_messages,
    ):
        if event["type"] not in {"run_start", "model_start"}:
            tool_events.append(event)
        if writer is not None:
            writer({"type": "code_agent_event", "event": event})
        if event["type"] == "final_answer":
            summary = event["content"]
        elif event["type"] == "error":
            error = event["message"]
        elif event["type"] == "tool_result" and not event["result"].get("ok"):
            error = event["result"].get("error", "CodeAgent tool failed")
        elif (
            event["type"] == "tool_result"
            and event.get("name") == "TodoUpdateTool"
            and event["result"].get("ok")
        ):
            todo_updated = True
    if summary and not error and not todo_updated:
        error = "CodeAgent must successfully call TodoUpdateTool"
    return {
        "ok": bool(summary) and not error,
        "summary": error or summary or "CodeAgent ended without a summary",
        "todos": tracker.snapshot(),
        "messages": captured_messages,
        "tool_events": tool_events,
    }
