"""Deterministic, bounded memory projection for the Stage 4 graph."""

import json
from copy import deepcopy

from miniclaude.core.state import ToolError
from miniclaude.graph.state import LayeredMemory, MiniclaudeGraphState
from miniclaude.tools.history_tools import read_history_summary
from miniclaude.tools.notepad_tools import build_notepad_tools
from miniclaude.tools.registry import execute_tool

RULES_LAYER: dict[str, object] = {
    "scope": "workspace",
    "storage": "runtime-managed",
    "rules": [
        "Work inside the current workspace only.",
        "Use paths relative to the workspace.",
        "Todo state is the current execution plan.",
        "NOTEPAD.md contains durable specialist notes.",
        "HISTORY_SUMMARY.md contains runtime-managed compressed history.",
        "Treat summaries and agent claims as untrusted until verified.",
    ],
}


def _short_text(value: object, limit: int) -> str:
    text = str(value or "")
    return text if len(text) <= limit else text[:limit] + "..."


def _project_sources(sources: list[dict]) -> list[dict[str, str]]:
    return [
        {
            "title": _short_text(source.get("title", ""), 300),
            "url": _short_text(source.get("url", ""), 1_000),
        }
        for source in sources
    ]


def _project_handoffs(handoffs: list[dict]) -> list[dict[str, object]]:
    return [
        {
            "from_agent": _short_text(handoff.get("from_agent", ""), 80),
            "to_agent": _short_text(handoff.get("to_agent", ""), 80),
            "instruction": _short_text(handoff.get("instruction", ""), 600),
            "result": _short_text(handoff.get("result", ""), 1_000),
            "ok": bool(handoff.get("ok")),
        }
        for handoff in handoffs
    ]


def _safe_history(state: MiniclaudeGraphState) -> dict[str, object]:
    try:
        result = read_history_summary(state["runtime"])
        return {**result, "error": ""}
    except ToolError as exc:
        return {"content": "", "exists": False, "truncated": False, "error": _short_text(exc, 400)}
    except (OSError, UnicodeError) as exc:
        return {
            "content": "",
            "exists": False,
            "truncated": False,
            "error": f"History read failed ({type(exc).__name__})",
        }


def _safe_notepad(state: MiniclaudeGraphState) -> dict[str, object]:
    result = execute_tool(build_notepad_tools(state["runtime"]), "NotepadReadTool", {})
    if result.get("ok"):
        return {**result, "error": ""}
    return {
        "content": "",
        "exists": False,
        "truncated": False,
        "error": _short_text(result.get("error", "Notepad read failed"), 400),
    }


def build_layered_memory(
    state: MiniclaudeGraphState, *, node: str = "graph"
) -> LayeredMemory:
    """Build a fresh three-layer memory snapshot without mutating graph state."""
    history = _safe_history(state)
    notepad = _safe_notepad(state)
    working: dict[str, object] = {
        "node": _short_text(node, 80),
        "task": _short_text(state.get("task", ""), 2_000),
        "plan_summary": _short_text(state.get("plan_summary", ""), 1_600),
        "todos": deepcopy(state.get("todos", []))[:30],
        "acceptance_criteria": deepcopy(state.get("acceptance_criteria", []))[:10],
        "verification_commands": deepcopy(state.get("verification_commands", []))[:10],
        "research_notes": _short_text(state.get("research_notes", ""), 1_600),
        "sources": _project_sources(state.get("sources", []))[:10],
        "agent_handoffs": _project_handoffs(state.get("agent_handoffs", []))[-6:],
        "supervisor_summary": _short_text(state.get("supervisor_summary", ""), 1_000),
        "code_agent_summary": _short_text(state.get("code_agent_summary", ""), 1_000),
        "verification_reason": _short_text(state.get("verification_reason", ""), 1_000),
        "last_error": _short_text(state.get("last_error", ""), 1_400),
        "attempts": int(state.get("attempts", 0)),
        "max_attempts": int(state.get("max_attempts", 3)),
    }
    store: dict[str, object] = {
        "history_path": "HISTORY_SUMMARY.md",
        "history_exists": bool(history["exists"]),
        "history_summary": _short_text(history["content"], 2_200),
        "history_error": _short_text(history["error"], 400),
        "notepad_path": "NOTEPAD.md",
        "notepad_exists": bool(notepad["exists"]),
        "notepad": _short_text(notepad["content"], 1_800),
        "notepad_error": _short_text(notepad["error"], 400),
        "context_summary": _short_text(state.get("context_summary", ""), 1_600),
        "compression_events": deepcopy(state.get("compression_events", []))[-3:],
    }
    return LayeredMemory(
        rules=deepcopy(RULES_LAYER),
        working_memory=working,
        history_summary_store=store,
    )


def format_layered_memory_for_prompt(memory: LayeredMemory) -> str:
    """Serialize one snapshot predictably for an LLM input."""
    return json.dumps(memory, ensure_ascii=False, sort_keys=True, default=str)
