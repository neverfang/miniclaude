"""Focused Textual widgets for the Stage 6 interface."""

from __future__ import annotations

import json

from rich.text import Text
from textual.containers import VerticalScroll
from textual.widgets import Static

from miniclaude.cli.tui.state import SessionViewState
from miniclaude.core.sanitize import sanitize_for_persistence

_MAX_CARD_TEXT = 8_000


def _bounded(value: object, limit: int = _MAX_CARD_TEXT) -> str:
    sanitized = sanitize_for_persistence(value)
    if isinstance(sanitized, str):
        text = sanitized
    else:
        text = json.dumps(sanitized, ensure_ascii=False, indent=2)
    return text[:limit]


class PlanPanel(Static):
    def update_plan(self, todos: object) -> None:
        if not isinstance(todos, list) or not todos:
            self.update(Text("Plan\n  Waiting for a workflow plan…", style="dim"))
            return
        lines = ["Plan"]
        icons = {"pending": "○", "in_progress": "◐", "completed": "●", "failed": "×"}
        for item in todos[:20]:
            if not isinstance(item, dict):
                continue
            status = str(item.get("status", "pending"))
            content = _bounded(item.get("content", ""), 300).replace("\n", " ")
            lines.append(f"  {icons.get(status, '·')} {content}")
        self.update(Text("\n".join(lines)))


class EventCard(Static):
    pass


class EventStream(VerticalScroll):
    def append_event(self, event: dict) -> None:
        source = event
        role = ""
        if event.get("type") == "react_event" and isinstance(event.get("event"), dict):
            source = event["event"]
            role = str(event.get("role", "agent"))
        kind = str(source.get("type", event.get("type", "event")))
        classes = {
            "tool_call": "tool-call-card",
            "tool_result": "tool-result-card",
            "handoff": "handoff-card",
            "checkpoint_saved": "checkpoint-card",
            "trace_started": "trace-card",
            "trace_summary": "trace-card",
            "error": "failure-card",
            "session_error": "failure-card",
        }.get(kind, "status-card")
        title = kind.replace("_", " ").title()
        if role:
            title = f"{role} · {title}"
        payload = {key: value for key, value in source.items() if key != "type"}
        body = _bounded(payload) if payload else "(no details)"
        self.mount(
            EventCard(
                Text(f"{title}\n{body}"),
                classes=f"event-card {classes}",
            )
        )
        self.scroll_end(animate=False)

    def clear_events(self) -> None:
        self.remove_children()


class ConversationPanel(Static):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._lines: list[str] = []

    def _refresh_content(self) -> None:
        content = "\n\n".join(self._lines) if self._lines else "Conversation"
        self.update(Text(content))

    def append_user(self, content: str) -> None:
        self._lines.append(f"You\n{_bounded(content)}")
        self._refresh_content()

    def append_assistant(self, content: str) -> None:
        self._lines.append(f"Miniclaude\n{_bounded(content)}")
        self._refresh_content()

    def clear_conversation(self) -> None:
        self._lines.clear()
        self._refresh_content()


class SessionSidebar(Static):
    def update_state(self, state: SessionViewState) -> None:
        workspace = _bounded(str(state.workspace), 64)
        token_value = (
            f"{state.context_tokens} / {state.context_limit}"
            if state.context_limit
            else "(waiting)"
        )
        pending = " (pending)" if state.approval_pending else ""
        lines = [
            "Session",
            "",
            f"status      {state.status}",
            f"turns       {state.turns}",
            f"session     {state.session_id}",
            f"route       {state.route}",
            f"workspace   {workspace}",
            f"checkpoint  {state.checkpoint}",
            f"trace       {state.trace_id}",
            f"tools       {state.tool_calls} total / {state.failed_tools} failed",
            f"approvals   {state.approvals}{pending}",
            f"tokens      {token_value}",
            f"todo        {state.todo_done} / {state.todo_total}",
        ]
        self.update(Text("\n".join(lines)))
