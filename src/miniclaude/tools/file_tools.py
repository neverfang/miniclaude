"""Bounded UTF-8 file access with optimistic read-before-write checks."""

import hashlib
from pathlib import Path

from miniclaude.core.paths import workspace_path
from miniclaude.core.state import RuntimeState, ToolError


def _load_text(state: RuntimeState, path: Path) -> tuple[str, str]:
    if not path.is_file():
        raise ToolError("File does not exist or is not a regular file")
    # A hard link could otherwise change a file whose other name is outside the workspace.
    if path.stat().st_nlink > 1:
        raise ToolError("Hard-linked files are not supported")
    with path.open("rb") as source:
        data = source.read(state.max_file_bytes + 1)
    if len(data) > state.max_file_bytes:
        raise ToolError(f"File exceeds {state.max_file_bytes} bytes")
    if b"\x00" in data:
        raise ToolError("Only UTF-8 text files are supported")
    try:
        return data.decode("utf-8"), hashlib.sha256(data).hexdigest()
    except UnicodeDecodeError as exc:
        raise ToolError("Only UTF-8 text files are supported") from exc


def _read_for_update(state: RuntimeState, path: Path) -> str:
    if path not in state.read_snapshots:
        raise ToolError("Use FileReadTool to read this file before editing or overwriting it")
    content, snapshot = _load_text(state, path)
    if state.read_snapshots[path] != snapshot:
        raise ToolError("File changed since last read; read it again before modifying it")
    return content


def _save_text(state: RuntimeState, path: Path, content: str):
    data = content.encode("utf-8")
    if len(data) > state.max_file_bytes or b"\x00" in data:
        raise ToolError("Content must be bounded UTF-8 text without NUL bytes")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    # Writing is not reading: force a fresh observation before a subsequent edit.
    state.read_snapshots.pop(path, None)


def read_file(state: RuntimeState, file_path: str, offset: int = 0, limit: int = 2000) -> dict:
    if offset < 0 or not 1 <= limit <= 2000:
        raise ToolError("offset must be >= 0 and limit must be between 1 and 2000")
    path = workspace_path(state, file_path)
    content, snapshot = _load_text(state, path)
    lines = content.splitlines()
    selection = lines[offset : offset + limit]
    rendered = []
    used = 0
    partial = False
    for index, line in enumerate(selection):
        numbered_line = f"{offset + index + 1}: {line}"
        cost = len(numbered_line) + bool(rendered)
        if used + cost > state.max_output_chars:
            if not rendered:
                rendered.append(numbered_line[: state.max_output_chars])
                partial = True
            break
        rendered.append(numbered_line)
        used += cost
    next_offset = min(len(lines), offset + len(rendered) - int(partial))
    state.read_snapshots[path] = snapshot
    return {
        "ok": True,
        "path": path.relative_to(state.workspace).as_posix(),
        "content": "\n".join(rendered),
        "total_lines": len(lines),
        "offset": offset,
        "lines_returned": len(rendered),
        "next_offset": next_offset,
        "partial_line": partial,
        "truncated": partial or next_offset < len(lines),
    }


def write_file(state: RuntimeState, file_path: str, content: str) -> dict:
    path = workspace_path(state, file_path)
    if path.exists():
        _read_for_update(state, path)
    _save_text(state, path, content)
    return {
        "ok": True,
        "path": path.relative_to(state.workspace).as_posix(),
        "bytes_written": len(content.encode("utf-8")),
    }


def edit_file(state: RuntimeState, file_path: str, old_text: str, new_text: str) -> dict:
    path = workspace_path(state, file_path)
    content = _read_for_update(state, path)
    if not old_text or content.count(old_text) != 1:
        raise ToolError("old_text must be nonempty and match exactly once; include more context")
    _save_text(state, path, content.replace(old_text, new_text, 1))
    return {"ok": True, "path": path.relative_to(state.workspace).as_posix(), "replacements": 1}
