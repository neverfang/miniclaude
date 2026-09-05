"""Fixed-path durable context summary managed by the Stage 4 runtime."""

from pathlib import Path

from miniclaude.core.paths import workspace_path
from miniclaude.core.state import RuntimeState, ToolError

HISTORY_FILE = "HISTORY_SUMMARY.md"
TEMP_HISTORY_FILE = ".HISTORY_SUMMARY.md.tmp"
MAX_HISTORY_BYTES = 65_536


def _assert_regular_unlinked(path: Path) -> None:
    if not path.is_file() or path.stat().st_nlink > 1:
        raise ToolError(f"{HISTORY_FILE} must be a regular non-linked file")


def read_history_summary(runtime: RuntimeState) -> dict[str, object]:
    """Read bounded UTF-8 history from the runtime-managed fixed path."""
    path = workspace_path(runtime, HISTORY_FILE)
    if not path.exists():
        return {
            "ok": True,
            "path": HISTORY_FILE,
            "content": "",
            "exists": False,
            "truncated": False,
        }
    _assert_regular_unlinked(path)
    data = path.read_bytes()
    if len(data) > MAX_HISTORY_BYTES or b"\x00" in data:
        raise ToolError(f"{HISTORY_FILE} is not bounded UTF-8 text")
    try:
        content = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ToolError(f"{HISTORY_FILE} is not bounded UTF-8 text") from exc
    return {
        "ok": True,
        "path": HISTORY_FILE,
        "content": content[: runtime.max_output_chars],
        "exists": True,
        "truncated": len(content) > runtime.max_output_chars,
    }


def persist_history_summary(runtime: RuntimeState, summary: str) -> dict[str, object]:
    """Atomically replace the runtime-managed fixed history file."""
    normalized = summary.strip()
    if not normalized:
        raise ToolError("history summary must be nonempty")
    if "\x00" in normalized:
        raise ToolError("history summary must not contain NUL bytes")
    data = normalized.encode("utf-8")
    if len(data) > MAX_HISTORY_BYTES:
        raise ToolError(f"{HISTORY_FILE} exceeds {MAX_HISTORY_BYTES} bytes")

    path = workspace_path(runtime, HISTORY_FILE)
    if path.exists():
        _assert_regular_unlinked(path)
    temporary = workspace_path(runtime, TEMP_HISTORY_FILE)
    if temporary.exists():
        _assert_regular_unlinked(temporary)
    try:
        temporary.write_bytes(data)
        temporary.replace(path)
    finally:
        if temporary.exists() and temporary.is_file():
            temporary.unlink()
    return {"ok": True, "path": HISTORY_FILE, "bytes": len(data)}
