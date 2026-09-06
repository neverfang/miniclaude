"""Rich terminal rendering for normalized miniclaude events."""

import json

from rich.console import Console, Group
from rich.panel import Panel
from rich.rule import Rule
from rich.text import Text

from miniclaude.core.sanitize import sanitize_for_persistence

DISPLAY_LIMIT = 2000


def _bounded(value: object, limit: int = DISPLAY_LIMIT) -> str:
    if isinstance(value, str):
        text = value
    else:
        text = json.dumps(value, ensure_ascii=False, indent=2, default=str)
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n... display truncated - original {len(text)} characters"


def _section(label: str, value: object, *, label_style: str = "bold") -> Group:
    body = value if isinstance(value, (Text, Group)) else Text(_bounded(value))
    return Group(Text(label, style=label_style), body)


def _stack(*items) -> Group:
    spaced = []
    for item in items:
        if item is None:
            continue
        if spaced:
            spaced.append(Text(""))
        spaced.append(item)
    return Group(*spaced)


def _bullet_lines(values: list, *, empty: str = "None") -> Text:
    if not values:
        return Text(empty, style="dim")
    lines = Text()
    for index, value in enumerate(values):
        if index:
            lines.append("\n")
        lines.append("- ", style="blue")
        lines.append(_bounded(value))
    return lines


def _todo_lines(todos: list[dict]) -> Text:
    if not todos:
        return Text("No todos", style="dim")
    icons = {
        "pending": ("[ ]", "dim"),
        "in_progress": ("[~]", "cyan"),
        "completed": ("[ok]", "green"),
        "blocked": ("[x]", "red"),
    }
    lines = Text()
    for index, todo in enumerate(todos):
        if index:
            lines.append("\n")
        icon, style = icons.get(str(todo.get("status")), ("-", "blue"))
        lines.append(f"{icon} ", style=style)
        lines.append(str(todo.get("content", todo.get("id", "Unnamed todo"))))
        note = todo.get("note")
        if note:
            lines.append(f" - {_bounded(note, 400)}", style="dim")
    return lines


def _check_lines(checks: list[dict]) -> Text:
    if not checks:
        return Text("No checks", style="dim")
    lines = Text()
    for index, check in enumerate(checks):
        if index:
            lines.append("\n")
        passed = bool(check.get("passed"))
        lines.append("[ok] " if passed else "[x] ", style="green" if passed else "red")
        lines.append(str(check.get("name", "Unnamed check")))
        detail = check.get("detail")
        if detail:
            lines.append(f" - {_bounded(detail, 500)}", style="dim")
    return lines


def _verification_results(results: list[dict]) -> Group | Text:
    if not results:
        return Text("No command results", style="dim")
    sections = []
    for result in results:
        ok = bool(result.get("ok"))
        header = Text()
        header.append("[ok] " if ok else "[x] ", style="green" if ok else "red")
        header.append(str(result.get("command", "Unknown command")))
        exit_code = result.get("exit_code")
        if exit_code is not None:
            header.append(f"  (exit {exit_code})", style="dim")
        details = [header]
        for key in ("stdout", "stderr"):
            value = result.get(key)
            if value:
                details.append(_section(key, value))
        if sections:
            sections.append(Text(""))
        sections.append(Group(*details))
    return Group(*sections)


def _tool_call(event: dict) -> Panel:
    args = event.get("args", {})
    values = args.items() if isinstance(args, dict) else [("arguments", args)]
    sections = [_section(str(key), value, label_style="bold magenta") for key, value in values]
    return Panel(
        Group(*sections) if sections else Text("No arguments", style="dim"),
        title=Text(
            f"Tool Call{' [' + str(event['_role']) + ']' if event.get('_role') else ''} - "
            f"{event.get('name', 'unknown')}"
        ),
        border_style="magenta",
        padding=(0, 1),
    )


def _tool_result(event: dict) -> Panel:
    result = event.get("result", {})
    ok = bool(result.get("ok")) if isinstance(result, dict) else False
    values = result.items() if isinstance(result, dict) else [("result", result)]
    sections = [
        _section(str(key), value, label_style="bold green" if ok else "bold red")
        for key, value in values
        if value not in (None, "")
    ]
    return Panel(
        Group(*sections) if sections else Text("No result details", style="dim"),
        title=Text(
            f"Tool Result{' [' + str(event['_role']) + ']' if event.get('_role') else ''} - "
            f"{event.get('name', 'unknown')}"
        ),
        border_style="green" if ok else "red",
        padding=(0, 1),
    )


def _planner(event: dict) -> Panel:
    body = _stack(
        Text(str(event.get("plan_summary", "No plan summary"))),
        _section("Todos", _todo_lines(event.get("todos", []))),
        _section("Acceptance criteria", _bullet_lines(event.get("acceptance_criteria", []))),
        _section("Verification commands", _bullet_lines(event.get("verification_commands", []))),
    )
    return Panel(
        body,
        title=Text(f"[planner] - Attempt {event.get('attempt', '?')}"),
        border_style="blue",
        padding=(0, 1),
    )


def _actor(event: dict) -> Panel:
    status = "COMPLETE" if event.get("ok") else "INCOMPLETE"
    body = _stack(
        Text(str(event.get("summary", "No actor summary"))),
        _section("Todos", _todo_lines(event.get("todos", []))),
    )
    return Panel(
        body,
        title=Text(f"[actor] - Attempt {event.get('attempt', '?')} - {status}"),
        border_style="cyan" if event.get("ok") else "yellow",
        padding=(0, 1),
    )


def _supervisor(event: dict) -> Panel:
    body = _stack(
        Text(str(event.get("plan_summary", "No plan summary"))),
        _section("Todos", _todo_lines(event.get("todos", []))),
        _section("Acceptance criteria", _bullet_lines(event.get("acceptance_criteria", []))),
        _section("Verification commands", _bullet_lines(event.get("verification_commands", []))),
        _section("Research notes", event.get("research_notes", "None")),
        _section("Sources", _bullet_lines(event.get("sources", []))),
    )
    return Panel(
        body,
        title=Text(f"[supervisor] - Attempt {event.get('attempt', '?')}"),
        border_style="blue",
        padding=(0, 1),
    )


def _handoff(event: dict) -> Panel:
    ok = bool(event.get("ok"))
    return Panel(
        _stack(
            _section("Instruction", event.get("instruction", "")),
            _section("Result", event.get("result", "")),
        ),
        title=Text(f"Handoff - {event.get('from_agent', '?')} -> {event.get('to_agent', '?')}"),
        border_style="green" if ok else "red",
        padding=(0, 1),
    )


def _specialist(event: dict, role: str) -> Panel:
    ok = bool(event.get("ok"))
    return Panel(
        Text(str(event.get("summary", "No summary"))),
        title=Text(f"[{role}] - {'COMPLETE' if ok else 'INCOMPLETE'}"),
        border_style="cyan" if ok else "yellow",
        padding=(0, 1),
    )


def _verifier(event: dict) -> Panel:
    passed = bool(event.get("passed"))
    status = "PASSED" if passed else "FAILED"
    body = _stack(
        Text(str(event.get("reason", "No verification reason"))),
        _section("Checks", _check_lines(event.get("checks", []))),
        _section("Command results", _verification_results(event.get("results", []))),
    )
    return Panel(
        body,
        title=Text(f"[verifier] - Attempt {event.get('attempt', '?')} - {status}"),
        border_style="green" if passed else "red",
        padding=(0, 1),
    )


def _context_monitor(event: dict) -> Panel:
    tokens = int(event.get("tokens", 0))
    limit = int(event.get("limit", 0))
    route = str(event.get("route", "unknown"))
    over_limit = limit > 0 and tokens >= limit
    body = _stack(
        _section("Usage", f"{tokens:,} / {limit:,} tokens"),
        _section("Counting method", event.get("method", "unknown")),
        _section("Next route", route),
    )
    return Panel(
        body,
        title=Text(f"Context Monitor - {'COMPRESS' if over_limit else 'READY'}"),
        border_style="yellow" if over_limit else "cyan",
        padding=(0, 1),
    )


def _context_compressor(event: dict) -> Panel:
    before = int(event.get("before_tokens", 0))
    after = int(event.get("after_tokens", 0))
    persistence_error = str(event.get("persistence_error", ""))
    body = _stack(
        _section("Reduction", f"{before:,} -> {after:,} tokens"),
        _section("Removed messages", event.get("removed_messages", 0)),
        _section("Counting method", event.get("count_method", "unknown")),
        _section("Fallback", "yes" if event.get("used_fallback") else "no"),
        _section("History persistence", persistence_error or "saved to HISTORY_SUMMARY.md"),
        _section("Next route", event.get("next_route", "unknown")),
    )
    return Panel(
        body,
        title=Text("Context Compressor"),
        border_style="yellow" if event.get("used_fallback") or persistence_error else "green",
        padding=(0, 1),
    )


def _final(event: dict) -> Panel:
    passed = bool(event.get("passed"))
    return Panel(
        Text(str(event.get("content", ""))),
        title=Text(f"[final] - {'SUCCESS' if passed else 'FAILURE'}"),
        border_style="green" if passed else "red",
        padding=(1, 2),
    )


def _safe_command(value: object) -> str:
    clean = sanitize_for_persistence({"command": value})
    if isinstance(clean, dict):
        return _bounded(clean.get("command", ""))
    return "[REDACTED]"


def _approval_requested(event: dict) -> Panel:
    return Panel(
        _stack(
            _section("Risk", event.get("risk_reason", "Unknown risk")),
            _section("Command", _safe_command(event.get("command", ""))),
        ),
        title=Text("Approval Required"),
        border_style="yellow",
        padding=(0, 1),
    )


def _approval_resolved(event: dict) -> Panel:
    approved = bool(event.get("approved"))
    return Panel(
        _stack(
            _section("Decision", "approved" if approved else "denied"),
            _section("Risk", event.get("risk_reason", "Unknown risk")),
        ),
        title=Text("Approval Granted" if approved else "Approval Denied"),
        border_style="green" if approved else "red",
        padding=(0, 1),
    )


def _checkpoint_saved(event: dict) -> Panel:
    return Panel(
        _stack(
            _section("Status", event.get("status", "unknown")),
            _section("Latest node", event.get("latest_node", "unknown")),
            _section("Artifact", event.get("path", ".miniclaude/checkpoints/checkpoint.json")),
            _section("Files", event.get("file_count", "unknown")),
            _section("Restorable", "yes" if event.get("snapshot_restorable") else "no"),
            _section("Snapshot", event.get("snapshot_error") or "available"),
        ),
        title=Text("Checkpoint Saved"),
        border_style="cyan",
        padding=(0, 1),
    )


def _resume_loaded(event: dict) -> Panel:
    return Panel(
        _stack(
            _section("Latest node", event.get("latest_node", "unknown")),
            _section("Resume node", event.get("resume_node", "contextual_supervisor")),
            _section("Workspace drift", "yes" if event.get("workspace_drift") else "no"),
            _section("Drift counts", event.get("drift", {})),
        ),
        title=Text("Resume Loaded"),
        border_style="yellow" if event.get("workspace_drift") else "green",
        padding=(0, 1),
    )


def _trace_started(event: dict) -> Panel:
    return Panel(
        _section("Trace ID", event.get("trace_id", "unknown")),
        title=Text("Trace Started"),
        border_style="cyan",
        padding=(0, 1),
    )


def _trace_summary(event: dict) -> Panel:
    return Panel(
        _stack(
            _section("Trace ID", event.get("trace_id", "unknown")),
            _section("Status", event.get("status", "unknown")),
            _section("Latest node", event.get("latest_node", "unknown")),
            _section("Duration", f"{event.get('duration_ms', 0)} ms"),
            _section("Node visits", event.get("node_visits", {})),
            _section("Tool calls", event.get("tool_calls", 0)),
            _section("Approvals", event.get("approval_count", 0)),
            _section("Checkpoints", event.get("checkpoint_count", 0)),
        ),
        title=Text("Trace Summary"),
        border_style="green" if event.get("status") == "passed" else "yellow",
        padding=(0, 1),
    )


def _harness_warning(event: dict) -> Panel:
    return Panel(
        Text(_bounded(event.get("message", "Harness persistence warning"))),
        title=Text("Harness Warning"),
        border_style="yellow",
        padding=(0, 1),
    )


EVENT_RENDERERS = {
    "approval_requested": _approval_requested,
    "approval_resolved": _approval_resolved,
    "checkpoint_saved": _checkpoint_saved,
    "checkpoint_warning": _harness_warning,
    "resume_loaded": _resume_loaded,
    "resume_warning": _harness_warning,
    "trace_started": _trace_started,
    "trace_summary": _trace_summary,
    "trace_warning": _harness_warning,
    "tool_call": _tool_call,
    "tool_result": _tool_result,
    "planner": _planner,
    "actor": _actor,
    "supervisor": _supervisor,
    "handoff": _handoff,
    "search_agent": lambda event: _specialist(event, "searchAgent"),
    "code_agent": lambda event: _specialist(event, "codeAgent"),
    "context_monitor": _context_monitor,
    "context_compressor": _context_compressor,
    "verifier": _verifier,
    "final": _final,
}


def render_event(console: Console, event: dict) -> None:
    """Render one event without interpreting untrusted Rich markup."""
    if event.get("_skip_render"):
        return
    kind = event.get("type", "unknown")
    if kind == "react_event":
        nested = event.get("event", {})
        role = event.get("role", "")
        if isinstance(nested, dict) and role not in {"", "actor"}:
            nested = {**nested, "_role": role}
        render_event(console, nested if isinstance(nested, dict) else {})
        return
    renderer = EVENT_RENDERERS.get(kind)
    if renderer is not None:
        console.print(renderer(event))
    elif kind == "run_start":
        line = Text("Workspace  ", style="bold cyan")
        line.append(str(event.get("workspace", "unknown")))
        console.print(line)
    elif kind == "model_start":
        console.print(Rule(Text(f"Model - Iteration {event.get('iteration', '?')}"), style="dim"))
    elif kind == "ai_message":
        console.print(Text(str(event.get("content", ""))))
    elif kind == "final_answer":
        console.print(
            Panel(
                Text(str(event.get("content", ""))),
                title=Text("Agent answer"),
                border_style="cyan",
            )
        )
    elif kind == "error":
        console.print(
            Panel(
                Text(str(event.get("message", "Unknown error"))),
                title=Text(f"Error - {event.get('code', 'unknown')}"),
                border_style="red",
            )
        )
    else:
        console.print(Text(f"Unsupported event: {kind}", style="dim"))
