"""Typed state shared by the stage-two workflow."""

from typing import Annotated, Literal, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages

from miniclaude.core.state import RuntimeState

TodoStatus = Literal["pending", "in_progress", "completed", "blocked"]


class TodoItem(TypedDict):
    id: str
    content: str
    status: TodoStatus
    note: str


class VerificationResult(TypedDict):
    command: str
    ok: bool
    exit_code: int | None
    stdout: str
    stderr: str


class VerificationCheck(TypedDict):
    name: str
    passed: bool
    detail: str


class MiniclaudeGraphState(TypedDict, total=False):
    task: str
    runtime: RuntimeState
    messages: Annotated[list[BaseMessage], add_messages]
    plan_summary: str
    todos: list[TodoItem]
    acceptance_criteria: list[str]
    verification_commands: list[str]
    verification_results: list[VerificationResult]
    verification_checks: list[VerificationCheck]
    passed: bool
    verification_reason: str
    last_error: str
    attempts: int
    max_attempts: int
    last_actor_summary: str
    final_answer: str


def initial_graph_state(
    task: str, *, runtime: RuntimeState, max_attempts: int = 3
) -> MiniclaudeGraphState:
    """Create a complete, validated state for one graph invocation."""
    if not task.strip():
        raise ValueError("task must not be empty")
    if not 1 <= max_attempts <= 10:
        raise ValueError("max_attempts must be between 1 and 10")
    return MiniclaudeGraphState(
        task=task.strip(),
        runtime=runtime,
        messages=[],
        plan_summary="",
        todos=[],
        acceptance_criteria=[],
        verification_commands=[],
        verification_results=[],
        verification_checks=[],
        passed=False,
        verification_reason="",
        last_error="",
        attempts=0,
        max_attempts=max_attempts,
        last_actor_summary="",
        final_answer="",
    )
