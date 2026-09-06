"""Conservative command-risk classification and approval contracts."""

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from uuid import uuid4

RiskLevel = Literal["safe", "risky", "blocked"]
MAX_COMMAND_LENGTH = 16_000


@dataclass(frozen=True)
class CommandRisk:
    level: RiskLevel
    reason: str
    segment: str = ""


@dataclass(frozen=True)
class ApprovalRequest:
    id: str
    command: str
    risk_level: RiskLevel
    risk_reason: str
    workspace: Path
    tool_name: str = "BashTool"


@dataclass(frozen=True)
class ApprovalDecision:
    approved: bool
    reason: str = ""


_BLOCKED_PATTERNS = (
    (re.compile(r"\bgit\s+reset\s+--hard\b", re.IGNORECASE), "Destructive Git reset"),
    (
        re.compile(r"\bgit\s+clean\b[^\r\n]*\s-[a-z]*[fdx][a-z]*", re.IGNORECASE),
        "Destructive Git clean",
    ),
    (
        re.compile(r"\brm\s+-(?:[a-z]*r[a-z]*f|[a-z]*f[a-z]*r)\s+[/~](?:\s|$)", re.IGNORECASE),
        "Broad recursive deletion",
    ),
    (
        re.compile(r"\bremove-item\b[^\r\n]*(?:-recurse\b|-r\b)", re.IGNORECASE),
        "Recursive PowerShell deletion",
    ),
    (re.compile(r"\b(?:shutdown|reboot)\b", re.IGNORECASE), "System shutdown or reboot"),
    (re.compile(r"\bformat(?:\.com)?\s+[a-z]:", re.IGNORECASE), "Filesystem formatting"),
)

_RISKY_PATTERNS = (
    (
        re.compile(r"(?:^|\s)(?:python\s+-m\s+)?pip\s+install\b", re.IGNORECASE),
        "Python package installation",
    ),
    (
        re.compile(r"(?:^|\s)uv\s+pip\s+install\b", re.IGNORECASE),
        "Python package installation with uv pip",
    ),
    (
        re.compile(r"(?:^|\s)uv\s+add\b", re.IGNORECASE),
        "Project dependency change with uv add",
    ),
    (
        re.compile(r"(?:^|\s)uv\s+sync\b", re.IGNORECASE),
        "Dependency synchronization with uv sync",
    ),
    (
        re.compile(r"(?:^|\s)(?:npm|pnpm)\s+install\b", re.IGNORECASE),
        "Node package installation",
    ),
    (
        re.compile(r"(?:^|\s)yarn\s+(?:install|add)\b", re.IGNORECASE),
        "Node package installation",
    ),
    (
        re.compile(r"(?:^|\s)(?:curl|wget)\b", re.IGNORECASE),
        "Network download command",
    ),
    (
        re.compile(r"(?:^|\s)uvicorn\b", re.IGNORECASE),
        "Long-running development server",
    ),
    (
        re.compile(r"(?:^|\s)python\s+-m\s+http\.server\b", re.IGNORECASE),
        "Long-running development server",
    ),
)

_NESTED_SHELL = re.compile(
    r"^\s*(?:(?:sh|bash|zsh)\s+-c|(?:powershell(?:\.exe)?|pwsh)\s+(?:-[^\s]+\s+)*-command|cmd(?:\.exe)?\s+/c)\s+(.+)$",
    re.IGNORECASE | re.DOTALL,
)
_OPAQUE_SHELL = re.compile(
    r"^\s*(?:powershell(?:\.exe)?|pwsh)\b[^\r\n]*\s-enc[a-z]*(?::|\s|$)",
    re.IGNORECASE,
)
_COMMAND_WRAPPER = re.compile(r"^\s*command\s+", re.IGNORECASE)
_ENV_WRAPPER = re.compile(r"^\s*env(?:\s+(?:-[^\s]+|[a-z_][a-z0-9_]*=[^\s]+))*\s+", re.IGNORECASE)
_DELETE_COMMAND = re.compile(r"^\s*(?:rm|remove-item|del|erase|rmdir|rd)\b", re.IGNORECASE)
_NAVIGATION_COMMAND = re.compile(r"^\s*(?:cd|chdir|pushd|set-location)\s+(.+)$", re.IGNORECASE)
_URL = re.compile(r"[a-z][a-z0-9+.-]*://[^\s'\"]+", re.IGNORECASE)
_VARIABLE_PATH = re.compile(r"(?:^|[\s<>=:'\"])(?:\$[a-z_{]|%[^%\s]+%)", re.IGNORECASE)
_PATH_TOKEN = re.compile(
    r"(?:^|[\s<>=:'\"])(?P<path>(?:[a-z]:[\\/]|\\\\|/|\.\.(?:[\\/]|$)|~[\\/])[^\s'\"<>|;&]*)",
    re.IGNORECASE,
)


def _strip_shell_wrappers(text: str) -> str:
    candidate = text
    while True:
        stripped = _COMMAND_WRAPPER.sub("", candidate, count=1)
        stripped = _ENV_WRAPPER.sub("", stripped, count=1)
        if stripped == candidate:
            return candidate.strip()
        candidate = stripped


def _path_escapes_workspace(text: str, workspace: Path | None) -> bool:
    searchable = _URL.sub("", text)
    if _VARIABLE_PATH.search(searchable):
        return True
    root = Path(workspace).resolve() if workspace is not None else None
    for match in _PATH_TOKEN.finditer(searchable):
        candidate = match.group("path").rstrip(",)")
        if candidate.startswith("~"):
            return True
        path = Path(candidate)
        if re.match(r"^[a-z]:[\\/]", candidate, re.IGNORECASE) and not path.is_absolute():
            return True
        if root is None:
            return True
        resolved = path.resolve() if path.is_absolute() else (root / path).resolve()
        if not resolved.is_relative_to(root):
            return True
    return False


def _unquote_payload(value: str) -> str:
    payload = value.strip()
    if len(payload) >= 2 and payload[0] == payload[-1] and payload[0] in {"'", '"'}:
        return payload[1:-1]
    return payload


def _split_shell_segments(command: str) -> list[tuple[str, str]]:
    """Return raw and quote-masked segments without interpreting the shell."""

    segments: list[tuple[str, str]] = []
    raw: list[str] = []
    searchable: list[str] = []
    quote = ""
    escaped = False

    def finish() -> None:
        raw_text = "".join(raw).strip()
        searchable_text = "".join(searchable).strip()
        if raw_text:
            segments.append((raw_text, searchable_text))
        raw.clear()
        searchable.clear()

    for character in command:
        if escaped:
            raw.append(character)
            searchable.append(" " if quote else character)
            escaped = False
            continue
        if character == "\\" and quote:
            raw.append(character)
            searchable.append(" ")
            escaped = True
            continue
        if quote:
            raw.append(character)
            searchable.append(" ")
            if character == quote:
                quote = ""
            continue
        if character in {"'", '"'}:
            quote = character
            raw.append(character)
            searchable.append(" ")
            continue
        if character in {"\n", "\r", ";", "|", "&"}:
            finish()
            continue
        raw.append(character)
        searchable.append(character)
    finish()
    return segments


def classify_command_risk(
    command: str,
    *,
    workspace: Path | None = None,
    _depth: int = 0,
) -> CommandRisk:
    """Classify a bounded command, preferring hard blocks over approval."""

    if "\x00" in command or not command.strip() or len(command) > MAX_COMMAND_LENGTH:
        return CommandRisk("blocked", "Malformed or oversized command", "")
    if _depth > 3:
        return CommandRisk("risky", "Nested shell command requires approval", command)

    first_risky: CommandRisk | None = None
    for raw, searchable in _split_shell_segments(command):
        candidate = _strip_shell_wrappers(raw)
        if _OPAQUE_SHELL.match(candidate):
            return CommandRisk("blocked", "Opaque nested shell payload", raw)
        nested = _NESTED_SHELL.match(candidate)
        if nested:
            nested_risk = classify_command_risk(
                _unquote_payload(nested.group(1)), workspace=workspace, _depth=_depth + 1
            )
            if nested_risk.level == "blocked":
                return CommandRisk("blocked", nested_risk.reason, raw)
            if first_risky is None:
                first_risky = CommandRisk(
                    "risky", nested_risk.reason or "Nested shell command requires approval", raw
                )
            continue
        if _path_escapes_workspace(raw, workspace):
            return CommandRisk("blocked", "Command path can escape workspace", raw)
        navigation = _NAVIGATION_COMMAND.match(raw)
        if navigation and _path_escapes_workspace(navigation.group(1), workspace):
            return CommandRisk("blocked", "Directory navigation can escape workspace", raw)
        for pattern, reason in _BLOCKED_PATTERNS:
            if pattern.search(searchable):
                return CommandRisk("blocked", reason, raw)
        if _DELETE_COMMAND.match(searchable):
            if first_risky is None:
                first_risky = CommandRisk("risky", "Workspace file deletion", raw)
        if first_risky is None:
            for pattern, reason in _RISKY_PATTERNS:
                if pattern.search(searchable):
                    first_risky = CommandRisk("risky", reason, raw)
                    break
    return first_risky or CommandRisk("safe", "", "")


def make_approval_request(
    command: str,
    risk: CommandRisk,
    workspace: Path,
) -> ApprovalRequest:
    """Create a unique immutable request for one exact command."""

    return ApprovalRequest(
        id=f"approval-{uuid4().hex[:8]}",
        command=command,
        risk_level=risk.level,
        risk_reason=risk.reason,
        workspace=Path(workspace).resolve(),
    )
