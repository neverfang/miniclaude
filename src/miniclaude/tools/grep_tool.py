"""Text search with bounded files, output and regular-expression execution time."""

import fnmatch
import os
import time
from pathlib import Path

import regex

from miniclaude.core.paths import protected_part, workspace_path
from miniclaude.core.state import RuntimeState, ToolError
from miniclaude.tools.file_tools import _load_text


def grep(
    state: RuntimeState,
    pattern: str,
    path: str = ".",
    glob: str = "*",
    head_limit: int = 50,
    ignore_case: bool = False,
) -> dict:
    if not pattern or len(pattern) > 1000 or not 1 <= head_limit <= 200:
        raise ToolError("Provide a nonempty pattern <= 1000 chars and head_limit from 1 to 200")
    try:
        expression = regex.compile(pattern, regex.IGNORECASE if ignore_case else 0)
    except regex.error as exc:
        raise ToolError("Invalid regular expression") from exc
    root = workspace_path(state, path)
    if not root.exists():
        raise ToolError("Search path does not exist")

    deadline = time.monotonic() + 3.0
    traversal_truncated = False

    def candidates():
        nonlocal traversal_truncated
        if root.is_file():
            yield root
            return
        for count, (directory, dirs, files) in enumerate(os.walk(root, followlinks=False), 1):
            if count > 2000 or time.monotonic() > deadline:
                traversal_truncated = True
                return
            allowed = []
            for name in sorted(dirs):
                try:
                    workspace_path(
                        state, (Path(directory) / name).relative_to(state.workspace).as_posix()
                    )
                except (ToolError, OSError):
                    continue
                allowed.append(name)
            dirs[:] = allowed
            for name in sorted(files):
                if not protected_part(name):
                    yield Path(directory) / name

    matches = []
    chars = 0
    inspected = 0
    skipped = 0
    truncated = False
    for candidate in candidates():
        inspected += 1
        if inspected > 2000 or time.monotonic() > deadline:
            truncated = True
            break
        relative = candidate.relative_to(state.workspace).as_posix()
        if not (fnmatch.fnmatchcase(relative, glob) or fnmatch.fnmatchcase(candidate.name, glob)):
            continue
        try:
            safe_path = workspace_path(state, relative)
            content, _ = _load_text(state, safe_path)
        except (ToolError, OSError):
            skipped += 1
            continue
        for number, line in enumerate(content.splitlines(), 1):
            if time.monotonic() > deadline:
                truncated = True
                break
            try:
                found = expression.search(line, timeout=0.05)
            except TimeoutError as exc:
                raise ToolError("Regular expression exceeded search time limit") from exc
            if found:
                if len(matches) >= head_limit or chars >= state.max_output_chars:
                    truncated = True
                    break
                text = line[: min(500, state.max_output_chars - chars)]
                chars += len(text)
                matches.append({"path": relative, "line": number, "text": text})
                if len(text) < len(line):
                    truncated = True
        if truncated:
            break
    return {
        "ok": True,
        "matches": matches,
        "truncated": truncated or traversal_truncated,
        "skipped_files": skipped,
    }
