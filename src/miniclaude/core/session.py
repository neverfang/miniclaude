"""Versioned, bounded persistence for Stage 6 interactive sessions."""

from __future__ import annotations

import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import TypedDict
from uuid import uuid4

from miniclaude.core.paths import protected_part
from miniclaude.core.sanitize import sanitize_for_persistence

SESSION_FORMAT_VERSION = 1
MAX_RECENT_TURNS = 20
MAX_TURN_CONTENT = 4_000
MAX_SESSION_CONTEXT = 7_000
SESSION_ID_PATTERN = re.compile(r"^[a-f0-9]{12}$")
_ROUTES = {"chat", "workflow"}
_ROLES = {"user", "assistant"}
_SUMMARY_LIMIT = 12_000


class SessionError(ValueError):
    """Raised when Session data cannot be safely created or loaded."""


class SessionData(TypedDict):
    format_version: int
    session_id: str
    turn_index: int
    recent_turns: list[dict[str, object]]
    created_at: str
    updated_at: str
    workspace: Path
    latest_checkpoint: str
    latest_trace_id: str


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _bounded(value: object, limit: int = MAX_TURN_CONTENT) -> str:
    sanitized = sanitize_for_persistence(str(value))
    text = sanitized if isinstance(sanitized, str) else str(sanitized)
    if len(text) <= limit:
        return text
    marker = "[TRUNCATED]"
    return text[: limit - len(marker)] + marker


def session_root(startup_directory: Path) -> Path:
    return Path(startup_directory).resolve() / ".miniclaude" / "sessions"


def _validate_session_id(session_id: str) -> None:
    if not isinstance(session_id, str) or not SESSION_ID_PATTERN.fullmatch(session_id):
        raise SessionError("Invalid session id; expected 12 lowercase hexadecimal characters")


def _session_directory(startup_directory: Path, session_id: str) -> Path:
    _validate_session_id(session_id)
    root = session_root(startup_directory)
    directory = (root / session_id).resolve()
    if not directory.is_relative_to(root.resolve()):
        raise SessionError("Invalid session id path")
    return directory


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _serialize(session: SessionData) -> dict[str, object]:
    return {
        "format_version": session["format_version"],
        "session_id": session["session_id"],
        "turn_index": session["turn_index"],
        "recent_turns": session["recent_turns"],
        "created_at": session["created_at"],
        "updated_at": session["updated_at"],
        "workspace": "workspace",
        "latest_checkpoint": session["latest_checkpoint"],
        "latest_trace_id": session["latest_trace_id"],
    }


def _render_summary(session: SessionData) -> str:
    lines = [
        f"# Session {session['session_id']}",
        "",
        f"- Updated: {session['updated_at']}",
        f"- Turns: {session['turn_index']}",
        f"- Latest checkpoint: {session['latest_checkpoint'] or '(none)'}",
        f"- Latest trace: {session['latest_trace_id'] or '(none)'}",
        "",
        "## Recent conversation",
        "",
    ]
    for item in session["recent_turns"]:
        role = str(item["role"])
        turn = int(item["turn"])
        route = f" ({item['route']})" if item.get("route") else ""
        content = item.get("summary") or item["content"]
        lines.extend((f"### Turn {turn} — {role}{route}", "", _bounded(content), ""))
    return _bounded("\n".join(lines), _SUMMARY_LIMIT)


def _load_index(root: Path) -> list[dict[str, str]]:
    path = root / "index.json"
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SessionError(f"Session index is unreadable ({type(exc).__name__})") from exc
    if not isinstance(raw, dict) or raw.get("format_version") != SESSION_FORMAT_VERSION:
        raise SessionError("Unsupported Session index version")
    entries = raw.get("sessions")
    if not isinstance(entries, list):
        raise SessionError("Session index has an invalid sessions list")
    result: list[dict[str, str]] = []
    for item in entries:
        if (
            not isinstance(item, dict)
            or set(item) != {"session_id", "updated_at"}
            or not isinstance(item.get("session_id"), str)
            or not isinstance(item.get("updated_at"), str)
        ):
            raise SessionError("Session index contains an invalid entry")
        _validate_session_id(item["session_id"])
        result.append(
            {"session_id": item["session_id"], "updated_at": item["updated_at"]}
        )
    return result


def _save_index(root: Path, session: SessionData) -> None:
    entries = [
        item
        for item in _load_index(root)
        if item["session_id"] != session["session_id"]
    ]
    entries.append(
        {
            "session_id": session["session_id"],
            "updated_at": session["updated_at"],
        }
    )
    entries.sort(key=lambda item: item["updated_at"], reverse=True)
    payload = {
        "format_version": SESSION_FORMAT_VERSION,
        "sessions": entries,
    }
    _atomic_write(
        root / "index.json",
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
    )


def create_session(startup_directory: Path) -> SessionData:
    root = session_root(startup_directory)
    root.mkdir(parents=True, exist_ok=True)
    for _ in range(20):
        session_id = uuid4().hex[:12]
        workspace = root / session_id / "workspace"
        try:
            workspace.mkdir(parents=True, exist_ok=False)
        except FileExistsError:
            continue
        now = _now()
        session = SessionData(
            format_version=SESSION_FORMAT_VERSION,
            session_id=session_id,
            turn_index=0,
            recent_turns=[],
            created_at=now,
            updated_at=now,
            workspace=workspace.resolve(),
            latest_checkpoint="",
            latest_trace_id="",
        )
        save_session(startup_directory, session)
        return session
    raise SessionError("Could not allocate a unique session id")


def append_user_turn(session: SessionData, content: str) -> int:
    if not isinstance(content, str) or not content.strip():
        raise SessionError("User turn content must not be blank")
    session["turn_index"] += 1
    turn = session["turn_index"]
    session["recent_turns"].append(
        {
            "role": "user",
            "turn": turn,
            "content": _bounded(content),
            "created_at": _now(),
        }
    )
    session["recent_turns"] = session["recent_turns"][-MAX_RECENT_TURNS:]
    return turn


def append_assistant_turn(
    session: SessionData,
    *,
    turn: int,
    route: str,
    content: str,
    summary: str = "",
) -> None:
    if turn != session["turn_index"] or turn < 1:
        raise SessionError("Assistant turn does not match the current user turn")
    if route not in _ROUTES:
        raise SessionError("Assistant route must be chat or workflow")
    if not isinstance(content, str) or not content.strip():
        raise SessionError("Assistant turn content must not be blank")
    item: dict[str, object] = {
        "role": "assistant",
        "turn": turn,
        "route": route,
        "content": _bounded(content),
        "created_at": _now(),
    }
    if summary:
        item["summary"] = _bounded(summary)
    session["recent_turns"].append(item)
    session["recent_turns"] = session["recent_turns"][-MAX_RECENT_TURNS:]


def save_session(startup_directory: Path, session: SessionData) -> None:
    _validate_in_memory_session(startup_directory, session)
    session["updated_at"] = _now()
    directory = _session_directory(startup_directory, session["session_id"])
    directory.mkdir(parents=True, exist_ok=True)
    session["workspace"].mkdir(parents=True, exist_ok=True)
    _atomic_write(
        directory / "session.json",
        json.dumps(_serialize(session), ensure_ascii=False, indent=2) + "\n",
    )
    _atomic_write(directory / "SESSION_SUMMARY.md", _render_summary(session) + "\n")
    _save_index(session_root(startup_directory), session)


def _validate_in_memory_session(
    startup_directory: Path,
    session: SessionData,
) -> None:
    required = {
        "format_version",
        "session_id",
        "turn_index",
        "recent_turns",
        "created_at",
        "updated_at",
        "workspace",
        "latest_checkpoint",
        "latest_trace_id",
    }
    if set(session) != required:
        raise SessionError("Session fields are invalid")
    if session["format_version"] != SESSION_FORMAT_VERSION:
        raise SessionError("Unsupported Session version")
    _validate_session_id(session["session_id"])
    if not isinstance(session["turn_index"], int) or session["turn_index"] < 0:
        raise SessionError("Session turn index is invalid")
    if not isinstance(session["recent_turns"], list):
        raise SessionError("Session recent turns are invalid")
    expected_workspace = (
        _session_directory(startup_directory, session["session_id"]) / "workspace"
    ).resolve()
    workspace = session["workspace"]
    if not isinstance(workspace, Path) or workspace.resolve() != expected_workspace:
        raise SessionError("Session workspace is invalid")
    for name in ("created_at", "updated_at", "latest_checkpoint", "latest_trace_id"):
        if not isinstance(session[name], str):
            raise SessionError(f"Session {name} is invalid")
    for item in session["recent_turns"]:
        if not isinstance(item, dict):
            raise SessionError("Session turn entry is invalid")
        if item.get("role") not in _ROLES:
            raise SessionError("Session turn role is invalid")
        if not isinstance(item.get("turn"), int) or int(item["turn"]) < 1:
            raise SessionError("Session turn number is invalid")
        if not isinstance(item.get("content"), str):
            raise SessionError("Session turn content is invalid")
        if len(item["content"]) > MAX_TURN_CONTENT:
            raise SessionError("Session turn content exceeds its bound")
        if item["role"] == "assistant" and item.get("route") not in _ROUTES:
            raise SessionError("Session assistant route is invalid")


def load_session(startup_directory: Path, session_id: str) -> SessionData:
    directory = _session_directory(startup_directory, session_id)
    path = directory / "session.json"
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SessionError(f"Session {session_id} does not exist") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise SessionError(f"Session data is unreadable ({type(exc).__name__})") from exc
    if not isinstance(raw, dict):
        raise SessionError("Session data must be an object")
    if raw.get("format_version") != SESSION_FORMAT_VERSION:
        raise SessionError("Unsupported Session version")
    if raw.get("workspace") != "workspace":
        raise SessionError("Session workspace record is invalid")
    raw["workspace"] = (directory / "workspace").resolve()
    session = SessionData(**raw)
    _validate_in_memory_session(startup_directory, session)
    return session


def load_latest_session(startup_directory: Path) -> SessionData:
    entries = _load_index(session_root(startup_directory))
    if not entries:
        raise SessionError(
            "No previous session exists. Run 'miniclaude' to create one first."
        )
    return load_session(startup_directory, entries[0]["session_id"])


def _is_link(path: Path) -> bool:
    return path.is_symlink() or (
        hasattr(path, "is_junction") and path.is_junction()
    )


def _safe_workspace_files(workspace: Path) -> list[str]:
    candidates: list[tuple[float, str]] = []
    for directory, names, filenames in os.walk(workspace, followlinks=False):
        current = Path(directory)
        names[:] = [
            name
            for name in names
            if not protected_part(name) and not _is_link(current / name)
        ]
        for filename in filenames:
            candidate = current / filename
            if protected_part(filename) or _is_link(candidate):
                continue
            try:
                if not candidate.is_file():
                    continue
                relative = candidate.relative_to(workspace).as_posix()
                modified = candidate.stat().st_mtime
            except (OSError, ValueError):
                continue
            if any(protected_part(part) for part in Path(relative).parts):
                continue
            candidates.append((modified, relative))
    candidates.sort(key=lambda item: (-item[0], item[1]))
    return [relative for _, relative in candidates[:30]]


def build_session_context(session: SessionData) -> str:
    """Build bounded context from recent summaries and safe workspace filenames."""

    files = _safe_workspace_files(session["workspace"])
    blocks = [
        f"Session: {session['session_id']}",
        f"Completed turns: {session['turn_index']}",
        "Workspace files (names only):",
        *(f"- {_bounded(path, 500)}" for path in files),
        "",
        "Recent conversation (newest first):",
    ]
    for item in reversed(session["recent_turns"][-10:]):
        role = str(item["role"])
        route = f"/{item['route']}" if item.get("route") else ""
        content = item.get("summary") or item["content"]
        blocks.extend(
            (
                f"[turn {item['turn']} {role}{route}]",
                _bounded(content),
            )
        )
    return "\n".join(blocks)[:MAX_SESSION_CONTEXT]
