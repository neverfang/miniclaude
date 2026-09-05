"""LangGraph wiring for stage two."""

from typing import Literal

from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph

from miniclaude.core.agent import ChatModel
from miniclaude.graph.nodes import (
    StructuredOutputModel,
    VerifierModel,
    make_actor_node,
    make_final_node,
    make_planner_node,
    make_verifier_node,
)
from miniclaude.graph.state import MiniclaudeGraphState


def _emit(event: dict) -> None:
    get_stream_writer()(event)


def _after_verifier(state: MiniclaudeGraphState) -> Literal["planner", "final"]:
    if state["passed"] or state["attempts"] >= state["max_attempts"]:
        return "final"
    return "planner"


def build_workflow(
    *,
    planner_model: StructuredOutputModel,
    actor_model: ChatModel,
    verifier_model: VerifierModel,
    actor_max_loops: int = 10,
    verifier_max_loops: int = 8,
):
    """Compile the bounded Plan → Execute → Verify graph."""
    builder = StateGraph(MiniclaudeGraphState)
    builder.add_node("planner", make_planner_node(planner_model, _emit))
    builder.add_node("actor", make_actor_node(actor_model, max_loops=actor_max_loops, emit=_emit))
    builder.add_node(
        "verifier", make_verifier_node(verifier_model, max_loops=verifier_max_loops, emit=_emit)
    )
    builder.add_node("final", make_final_node(_emit))
    builder.add_edge(START, "planner")
    builder.add_edge("planner", "actor")
    builder.add_edge("actor", "verifier")
    builder.add_conditional_edges("verifier", _after_verifier)
    builder.add_edge("final", END)
    return builder.compile(name="miniclaude-stage-two")
