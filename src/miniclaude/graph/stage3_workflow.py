"""LangGraph wiring for the stage-three MultiAgent workflow."""

from collections.abc import Callable
from typing import Literal

from langchain_core.tools import StructuredTool
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph

from miniclaude.agents.code_agent import run_code_agent
from miniclaude.agents.search_agent import run_search_agent
from miniclaude.core.agent import ChatModel
from miniclaude.graph.nodes import VerifierModel, make_final_node, make_verifier_node
from miniclaude.graph.state import MiniclaudeGraphState, VerificationResult
from miniclaude.graph.supervisor import make_supervisor_node
from miniclaude.prompts.stage3 import STAGE3_VERIFIER_PROMPT
from miniclaude.tools.notepad_tools import build_notepad_tools
from miniclaude.tools.registry import execute_tool


def _emit(event: dict) -> None:
    get_stream_writer()(event)


def _after_verifier(state: MiniclaudeGraphState) -> Literal["supervisor", "final"]:
    if state["passed"] or state["attempts"] >= state["max_attempts"]:
        return "final"
    return "supervisor"


def _notepad_read_tools(runtime):
    return [tool for tool in build_notepad_tools(runtime) if tool.name == "NotepadReadTool"]


def _stage3_context(state: MiniclaudeGraphState, results: list[VerificationResult]) -> dict:
    read_tools = _notepad_read_tools(state["runtime"])
    note = execute_tool(read_tools, "NotepadReadTool", {})
    return {
        "sources_untrusted": state.get("sources", [])[:20],
        "agent_handoffs_untrusted": state.get("agent_handoffs", [])[-20:],
        "research_notes_untrusted": state.get("research_notes", ""),
        "supervisor_summary_untrusted": state.get("supervisor_summary", ""),
        "code_agent_summary_untrusted": state.get("code_agent_summary", ""),
        "notepad_untrusted": note.get("content", "") if note.get("ok") else "",
    }


def build_stage3_workflow(
    *,
    supervisor_model: ChatModel,
    verifier_model: VerifierModel,
    web_search_tool: StructuredTool,
    supervisor_max_loops: int = 10,
    verifier_max_loops: int = 8,
    search_runner: Callable = run_search_agent,
    code_runner: Callable = run_code_agent,
):
    """Compile Supervisor → Verifier with bounded repair attempts."""
    builder = StateGraph(MiniclaudeGraphState)
    builder.add_node(
        "supervisor",
        make_supervisor_node(
            supervisor_model,
            web_search_tool,
            max_loops=supervisor_max_loops,
            search_runner=search_runner,
            code_runner=code_runner,
            emit=_emit,
        ),
    )
    builder.add_node(
        "verifier",
        make_verifier_node(
            verifier_model,
            max_loops=verifier_max_loops,
            emit=_emit,
            system_prompt=STAGE3_VERIFIER_PROMPT,
            context_builder=_stage3_context,
            extra_readonly_tools=_notepad_read_tools,
            block_on_prior_error=True,
        ),
    )
    builder.add_node("final", make_final_node(_emit))
    builder.add_edge(START, "supervisor")
    builder.add_edge("supervisor", "verifier")
    builder.add_conditional_edges("verifier", _after_verifier)
    builder.add_edge("final", END)
    return builder.compile(name="miniclaude-stage-three")
