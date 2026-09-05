"""LangGraph wiring for Stage 4 context engineering."""

from collections.abc import Callable
from typing import Literal

from langchain_core.tools import StructuredTool
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph

from miniclaude.agents.code_agent import run_code_agent
from miniclaude.agents.search_agent import run_search_agent
from miniclaude.core.agent import ChatModel
from miniclaude.graph.context import (
    context_monitor_route,
    make_context_compressor_node,
    make_context_monitor_node,
)
from miniclaude.graph.memory import build_layered_memory
from miniclaude.graph.nodes import VerifierModel, make_final_node, make_verifier_node
from miniclaude.graph.state import LayeredMemory, MiniclaudeGraphState, VerificationResult
from miniclaude.graph.supervisor import make_supervisor_node
from miniclaude.prompts.stage3 import STAGE3_VERIFIER_PROMPT
from miniclaude.tools.notepad_tools import build_notepad_tools


def _emit(event: dict) -> None:
    get_stream_writer()(event)


def _empty_memory() -> LayeredMemory:
    return LayeredMemory(rules={}, working_memory={}, history_summary_store={})


def _after_verifier(state: MiniclaudeGraphState) -> Literal["contextual_supervisor", "final"]:
    if state.get("passed") or state.get("attempts", 0) >= state.get("max_attempts", 3):
        return "final"
    return "contextual_supervisor"


def _after_compressor(
    state: MiniclaudeGraphState,
) -> Literal["contextual_supervisor", "final"]:
    return "final" if state.get("context_next_node") == "final" else "contextual_supervisor"


def _notepad_read_tools(runtime):
    return [tool for tool in build_notepad_tools(runtime) if tool.name == "NotepadReadTool"]


def _stage4_verifier_context(
    state: MiniclaudeGraphState, results: list[VerificationResult]
) -> dict:
    return {
        "context_summary_untrusted": state.get("context_summary", ""),
        "layered_memory_untrusted": state.get("memory_snapshot", {}),
        "sources_untrusted": state.get("sources", [])[:20],
        "agent_handoffs_untrusted": state.get("agent_handoffs", [])[-20:],
    }


def _contextual_supervisor(
    supervisor_node: Callable[[MiniclaudeGraphState], dict],
):
    def contextual(state: MiniclaudeGraphState) -> dict:
        memory_error = ""
        try:
            memory = build_layered_memory(state, node="supervisor")
        except Exception as exc:
            memory = _empty_memory()
            memory_error = f"Layered memory failed ({type(exc).__name__})"
        augmented = dict(state)
        augmented["memory_snapshot"] = memory
        augmented["history_summary"] = str(
            memory["history_summary_store"].get("history_summary", "")
        )
        try:
            update = supervisor_node(augmented)
        except Exception as exc:
            error = f"Supervisor failed ({type(exc).__name__})"
            return {
                "memory_snapshot": memory,
                "history_summary": augmented["history_summary"],
                "context_next_node": "final",
                "context_error": error,
                "last_error": error,
            }
        return {
            **update,
            "memory_snapshot": memory,
            "history_summary": augmented["history_summary"],
            "context_next_node": "verifier",
            "context_error": memory_error,
        }

    return contextual


def _safe_compressor(compressor_node: Callable[[MiniclaudeGraphState], dict]):
    def compress(state: MiniclaudeGraphState) -> dict:
        try:
            return compressor_node(state)
        except Exception as exc:
            error = f"Context compressor failed ({type(exc).__name__})"
            return {
                "context_should_compress": False,
                "context_next_node": "final",
                "context_error": error,
                "last_error": error,
            }

    return compress


def _stage4_final():
    final_node = make_final_node(_emit)

    def final(state: MiniclaudeGraphState) -> dict:
        if state.get("context_error") and not state.get("passed"):
            augmented = dict(state)
            augmented["last_error"] = state["context_error"]
            return final_node(augmented)
        return final_node(state)

    return final


def build_stage4_workflow(
    *,
    supervisor_model: ChatModel,
    verifier_model: VerifierModel,
    web_search_tool: StructuredTool,
    context_counter: object | None = None,
    supervisor_max_loops: int = 10,
    verifier_max_loops: int = 8,
    search_runner: Callable = run_search_agent,
    code_runner: Callable = run_code_agent,
):
    """Compile Stage 3 roles with bounded context monitoring and compression."""
    counter = context_counter or supervisor_model
    supervisor = make_supervisor_node(
        supervisor_model,
        web_search_tool,
        max_loops=supervisor_max_loops,
        search_runner=search_runner,
        code_runner=code_runner,
        emit=_emit,
    )
    compressor = make_context_compressor_node(supervisor_model, counter, emit=_emit)

    builder = StateGraph(MiniclaudeGraphState)
    builder.add_node("contextual_supervisor", _contextual_supervisor(supervisor))
    builder.add_node("context_monitor", make_context_monitor_node(counter, emit=_emit))
    builder.add_node("context_compressor", _safe_compressor(compressor))
    builder.add_node(
        "verifier",
        make_verifier_node(
            verifier_model,
            max_loops=verifier_max_loops,
            emit=_emit,
            system_prompt=STAGE3_VERIFIER_PROMPT,
            context_builder=_stage4_verifier_context,
            extra_readonly_tools=_notepad_read_tools,
            block_on_prior_error=True,
        ),
    )
    builder.add_node("final", _stage4_final())

    builder.add_edge(START, "contextual_supervisor")
    builder.add_edge("contextual_supervisor", "context_monitor")
    builder.add_conditional_edges(
        "context_monitor",
        context_monitor_route,
        {
            "verifier": "verifier",
            "compressor": "context_compressor",
            "final": "final",
        },
    )
    builder.add_conditional_edges("context_compressor", _after_compressor)
    builder.add_conditional_edges("verifier", _after_verifier)
    builder.add_edge("final", END)
    return builder.compile(name="miniclaude-stage-four")
