"""Deterministic reducer for the Stage 6 Session sidebar."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path


@dataclass(frozen=True, slots=True)
class SessionViewState:
    session_id: str
    workspace: Path
    status: str = "idle"
    turns: int = 0
    route: str = "(none)"
    shell_enabled: bool = False
    approval_mode: str = "inline"
    checkpoint: str = "(waiting)"
    trace_id: str = "(waiting)"
    tool_calls: int = 0
    failed_tools: int = 0
    approvals: int = 0
    approval_pending: bool = False
    context_tokens: int = 0
    context_limit: int = 0
    todo_done: int = 0
    todo_total: int = 0


def initial_session_view(
    session_id: str,
    workspace: Path,
    *,
    turns: int = 0,
    shell_enabled: bool = False,
    approval_mode: str = "inline",
) -> SessionViewState:
    return SessionViewState(
        session_id=session_id,
        workspace=Path(workspace),
        turns=turns,
        shell_enabled=shell_enabled,
        approval_mode=approval_mode,
    )


def _todo_counts(todos: object) -> tuple[int, int]:
    if not isinstance(todos, list):
        return 0, 0
    rows = [item for item in todos if isinstance(item, dict)]
    return (
        sum(item.get("status") == "completed" for item in rows),
        len(rows),
    )


def reduce_session_event(
    state: SessionViewState,
    event: dict,
) -> SessionViewState:
    kind = event.get("type")
    changes: dict[str, object] = {}

    if kind == "session_status":
        changes["status"] = str(event.get("status", state.status))
    elif kind == "intent_decision":
        changes["route"] = str(event.get("route", state.route))
    elif kind == "runtime_policy":
        changes.update(
            shell_enabled=bool(event.get("shell_enabled", state.shell_enabled)),
            approval_mode=str(event.get("approval_mode", state.approval_mode)),
        )
    elif kind == "approval_requested":
        changes.update(
            status="waiting approval",
            approvals=state.approvals + 1,
            approval_pending=True,
        )
    elif kind == "approval_resolved":
        changes["approval_pending"] = False
        changes["status"] = "running"
    elif kind == "checkpoint_saved":
        changes["checkpoint"] = str(
            event.get("checkpoint_id") or event.get("path") or "(saved)"
        )
    elif kind in {"trace_started", "trace_summary"}:
        changes["trace_id"] = str(event.get("trace_id") or state.trace_id)
    elif kind in {"planner", "supervisor", "actor"}:
        done, total = _todo_counts(event.get("todos"))
        if total:
            changes.update(todo_done=done, todo_total=total)
        if kind == "planner":
            changes["status"] = "planning"
    elif kind == "verifier":
        changes["status"] = "verifying"
    elif kind in {"context_monitor", "context_compressor"}:
        current = event.get("context_token_count")
        limit = event.get("context_token_limit")
        if isinstance(current, int):
            changes["context_tokens"] = current
        if isinstance(limit, int):
            changes["context_limit"] = limit
    elif kind == "react_event":
        nested = event.get("event")
        if isinstance(nested, dict) and nested.get("type") == "tool_result":
            result = nested.get("result")
            ok = result.get("ok") if isinstance(result, dict) else nested.get("ok")
            changes["tool_calls"] = state.tool_calls + 1
            if ok is False:
                changes["failed_tools"] = state.failed_tools + 1
    elif kind == "session_final":
        changes.update(
            status="completed" if event.get("passed", True) else "failed",
            route=str(event.get("route", state.route)),
            turns=max(state.turns, int(event.get("turn", state.turns + 1))),
            approval_pending=False,
        )
    elif kind == "session_error":
        changes.update(status="failed", approval_pending=False)
    elif kind == "session_cancelling":
        changes.update(status="cancelling", approval_pending=False)
    elif kind == "session_cancelled":
        changes.update(status="idle", approval_pending=False)

    return replace(state, **changes) if changes else state
