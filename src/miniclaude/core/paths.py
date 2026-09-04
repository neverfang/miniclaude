"""Conservative file-tool boundaries; these are NOT a Shell sandbox."""

import re
from pathlib import Path, PureWindowsPath

from miniclaude.core.state import RuntimeState, ToolError

IGNORED_DIRS = {".git", ".venv", "venv", "node_modules", ".miniclaude", "__pycache__"}


def protected_part(part: str) -> bool:
    name = part.casefold()
    return (
        name in IGNORED_DIRS
        or name == ".env"
        or name.startswith(".env.")
        or name.endswith((".pem", ".key"))
    )


def workspace_path(state: RuntimeState, file_path: str) -> Path:
    """Resolve relative paths, checking both lexical and resolved components."""
    if not file_path or "\x00" in file_path or len(file_path) > 4096:
        raise ToolError("A nonempty workspace-relative path is required")
    normalized = file_path.replace("\\", "/")
    win_path = PureWindowsPath(file_path)
    if Path(normalized).is_absolute() or win_path.drive or normalized.startswith("/"):
        raise ToolError("Use a workspace-relative path, not an absolute path")
    parts = Path(normalized).parts
    for part in parts:
        if (
            part == ".."
            or ":" in part
            or part.rstrip(" .") != part
            or protected_part(part)
            or re.fullmatch(r"(?i)(con|prn|aux|nul|com[1-9]|lpt[1-9])(?:\..*)?", part)
        ):
            raise ToolError("Path is outside the allowed workspace scope or is protected")
    target = state.workspace.joinpath(*parts)
    current = state.workspace
    for part in parts:
        current = current / part
        # Refusing all links is easier to reason about than following mutable aliases.
        if current.is_symlink() or (hasattr(current, "is_junction") and current.is_junction()):
            raise ToolError("Symbolic links and junctions are not supported by file tools")
    resolved = target.resolve()
    if not resolved.is_relative_to(state.workspace):
        raise ToolError("Path escapes the workspace")
    return resolved
