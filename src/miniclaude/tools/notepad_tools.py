"""Fixed-path durable notes for the CodeAgent."""

from langchain_core.tools import StructuredTool

from miniclaude.core.paths import workspace_path
from miniclaude.core.state import RuntimeState, ToolError


def build_notepad_tools(runtime: RuntimeState, *, max_bytes: int = 65_536) -> list[StructuredTool]:
    if max_bytes < 1:
        raise ValueError("max_bytes must be positive")

    def append(note: str) -> dict:
        """Append one durable decision or finding to the fixed workspace NOTEPAD.md."""
        if not note.strip() or "\x00" in note:
            raise ToolError("note must be nonempty UTF-8 text without NUL bytes")
        path = workspace_path(runtime, "NOTEPAD.md")
        if path.exists() and (not path.is_file() or path.stat().st_nlink > 1):
            raise ToolError("NOTEPAD.md must be a regular non-linked file")
        existing = path.read_bytes() if path.exists() else b""
        addition = (note.rstrip("\r\n") + "\n").encode("utf-8")
        if len(existing) + len(addition) > max_bytes:
            raise ToolError(f"NOTEPAD.md exceeds {max_bytes} bytes")
        path.write_bytes(existing + addition)
        return {"ok": True, "path": "NOTEPAD.md", "bytes": len(existing) + len(addition)}

    def read(limit: int | None = None) -> dict:
        """Read bounded durable context from the fixed workspace NOTEPAD.md."""
        path = workspace_path(runtime, "NOTEPAD.md")
        if not path.exists():
            return {
                "ok": True,
                "path": "NOTEPAD.md",
                "content": "",
                "exists": False,
                "truncated": False,
            }
        if not path.is_file() or path.stat().st_nlink > 1:
            raise ToolError("NOTEPAD.md must be a regular non-linked file")
        data = path.read_bytes()
        if len(data) > max_bytes or b"\x00" in data:
            raise ToolError("NOTEPAD.md is not bounded UTF-8 text")
        try:
            content = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ToolError("NOTEPAD.md is not bounded UTF-8 text") from exc
        output_limit = runtime.max_output_chars if limit is None else limit
        if not 1 <= output_limit <= runtime.max_output_chars:
            raise ToolError(f"limit must be between 1 and {runtime.max_output_chars}")
        return {
            "ok": True,
            "path": "NOTEPAD.md",
            "content": content[:output_limit],
            "exists": True,
            "truncated": len(content) > output_limit,
        }

    return [
        StructuredTool.from_function(append, name="NotepadAppendTool"),
        StructuredTool.from_function(read, name="NotepadReadTool"),
    ]
