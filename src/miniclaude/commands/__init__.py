"""Local slash-command contracts for the interactive TUI."""

from miniclaude.commands.registry import (
    CommandContext,
    CommandRegistry,
    CommandResult,
    CommandSpec,
    ParsedCommand,
    build_command_registry,
)

__all__ = [
    "CommandContext",
    "CommandRegistry",
    "CommandResult",
    "CommandSpec",
    "ParsedCommand",
    "build_command_registry",
]
