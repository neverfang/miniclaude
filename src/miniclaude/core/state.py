"""Runtime data shared by tools during one invocation (not persistent memory)."""

from dataclasses import dataclass, field
from pathlib import Path


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

    def __post_init__(self):
        self.workspace = Path(self.workspace).resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)
        if not self.workspace.is_dir():
            raise ValueError("workspace must be a directory")
