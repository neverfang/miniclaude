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


class SourceItem(TypedDict):
    title: str
    url: str
    content: str
    score: float | None


class AgentHandoff(TypedDict):
    from_agent: str
    to_agent: str
    instruction: str
    result: str
    ok: bool


class CompressionEvent(TypedDict):
    before_tokens: int
    after_tokens: int
    removed_messages: int
    attempt: int
    used_fallback: bool


class LayeredMemory(TypedDict):
    rules: dict[str, object]
    working_memory: dict[str, object]
    history_summary_store: dict[str, object]


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
    research_notes: str
    sources: list[SourceItem]
    agent_handoffs: list[AgentHandoff]
    code_agent_summary: str
    supervisor_summary: str
    context_summary: str
    context_token_count: int
    context_token_limit: int
    context_should_compress: bool
    context_next_node: str
    context_error: str
    context_count_method: str
    compression_events: list[CompressionEvent]
    memory_snapshot: LayeredMemory
    history_summary: str


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
        research_notes="",
        sources=[],
        agent_handoffs=[],
        code_agent_summary="",
        supervisor_summary="",
        context_summary="",
        context_token_count=0,
        context_token_limit=400_000,
        context_should_compress=False,
        context_next_node="verifier",
        context_error="",
        context_count_method="",
        compression_events=[],
        memory_snapshot={},
        history_summary="",
    )
