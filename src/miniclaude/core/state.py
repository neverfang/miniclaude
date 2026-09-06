"""Runtime data shared by tools during one invocation (not persistent memory)."""

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal


class ToolError(ValueError):
    """An actionable tool error that may safely be returned to the model."""


@dataclass
class RuntimeState:
    workspace: Path
    allow_shell: bool = False
    max_file_bytes: int = 1_048_576
    max_output_chars: int = 12_000
    command_timeout: float = 30.0
    read_snapshots: dict[Path, str] = field(default_factory=dict)

    approval_mode: Literal["inline", "auto", "deny"] = "inline"
    approval_handler: Callable[[Any], Any] | None = None
    checkpoint_mode: Literal["light", "strict", "off"] = "light"
    trace_mode: Literal["on", "off"] = "on"
    trace_id: str | None = None
    event_handler: Callable[[dict[str, object]], None] | None = None
    def __post_init__(self):
        self.workspace = Path(self.workspace).resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)
        if not self.workspace.is_dir():
            raise ValueError("workspace must be a directory")
        if self.approval_mode not in {"inline", "auto", "deny"}:
            raise ValueError("approval mode must be inline, auto, or deny")
        if self.checkpoint_mode not in {"light", "strict", "off"}:
            raise ValueError("checkpoint mode must be light, strict, or off")
        if self.trace_mode not in {"on", "off"}:
            raise ValueError("trace mode must be on or off")
