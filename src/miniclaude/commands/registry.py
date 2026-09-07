"""UI-independent registry for bounded local slash commands."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

MAX_COMMAND_CHARS = 4_096


@dataclass(frozen=True)
class ParsedCommand:
    name: str
    arguments: str = ""


@dataclass(frozen=True)
class CommandContext:
    session_id: str
    workspace: Path
    status: str
    route: str
    turn_active: bool
    allow_shell: bool
    approval_mode: str
    tool_names: tuple[str, ...] = ()
    skill_names: tuple[str, ...] = ()
    mcp_servers: tuple[str, ...] = ()
    active_skill: str = ""


@dataclass(frozen=True)
class CommandResult:
    ok: bool
    message: str
    action: str | None = None


CommandHandler = Callable[[CommandContext, str], CommandResult]


@dataclass(frozen=True)
class CommandSpec:
    name: str
    usage: str
    description: str
    handler: CommandHandler
    aliases: tuple[str, ...] = ()
    allowed_while_active: bool = True


class CommandRegistry:
    """Resolve and execute local commands without interpreting their arguments."""

    def __init__(self, specs: tuple[CommandSpec, ...]):
        self.specs = specs
        self._names: dict[str, CommandSpec] = {}
        for spec in specs:
            for name in (spec.name, *spec.aliases):
                key = name.casefold()
                if key in self._names:
                    raise ValueError(f"Duplicate slash command: {name}")
                self._names[key] = spec

    def parse(self, raw: str) -> ParsedCommand:
        if not isinstance(raw, str):
            raise ValueError("Slash command must be text")
        text = raw.strip()
        if text.startswith("／"):
            text = "/" + text[1:]
        if not text.startswith("/"):
            raise ValueError("Slash command must start with /")
        if len(text) > MAX_COMMAND_CHARS:
            raise ValueError(f"Slash command exceeds {MAX_COMMAND_CHARS} characters")
        payload = text[1:].strip()
        if not payload:
            return ParsedCommand("help")
        name, separator, arguments = payload.partition(" ")
        return ParsedCommand(name.casefold(), arguments.strip() if separator else "")

    def execute(self, raw: str, context: CommandContext) -> CommandResult:
        try:
            parsed = self.parse(raw)
        except ValueError as exc:
            return CommandResult(False, str(exc))
        spec = self._names.get(parsed.name)
        if spec is None:
            return CommandResult(False, f"Unknown command: /{parsed.name}. Use /help.")
        if context.turn_active and not spec.allowed_while_active:
            return CommandResult(False, f"/{spec.name} is unavailable while a turn is running.")
        return spec.handler(context, parsed.arguments)

    def suggest(self, raw: str, *, limit: int = 8) -> tuple[CommandSpec, ...]:
        text = raw.strip()
        if text.startswith("／"):
            text = "/" + text[1:]
        if not text.startswith("/") or " " in text or len(text) > MAX_COMMAND_CHARS:
            return ()
        prefix = text[1:].casefold()
        return tuple(spec for spec in self.specs if spec.name.startswith(prefix))[:limit]


def _result(message: str, action: str | None = None) -> CommandResult:
    return CommandResult(True, message, action)


def build_command_registry() -> CommandRegistry:
    """Build the stable built-in command set."""

    specs: list[CommandSpec] = []

    def register(
        name: str,
        description: str,
        handler: CommandHandler,
        *,
        usage: str | None = None,
        aliases: tuple[str, ...] = (),
        allowed_while_active: bool = True,
    ) -> None:
        specs.append(
            CommandSpec(
                name=name,
                usage=usage or f"/{name}",
                description=description,
                handler=handler,
                aliases=aliases,
                allowed_while_active=allowed_while_active,
            )
        )

    def help_command(context: CommandContext, arguments: str) -> CommandResult:
        del context, arguments
        lines = ["Available local commands:"]
        lines.extend(f"{spec.usage:<18} {spec.description}" for spec in specs)
        return _result("\n".join(lines))

    def status(context: CommandContext, arguments: str) -> CommandResult:
        del arguments
        return _result(
            "\n".join(
                (
                    f"Session: {context.session_id}",
                    f"Status: {context.status}",
                    f"Route: {context.route}",
                    f"Workspace: {context.workspace}",
                    f"Shell: {'enabled' if context.allow_shell else 'disabled'}",
                    f"Approval mode: {context.approval_mode}",
                )
            )
        )

    def approve(context: CommandContext, arguments: str) -> CommandResult:
        mode = arguments.strip().casefold()
        if not mode:
            state = "enabled" if context.allow_shell else "disabled"
            return _result(
                f"Shell: {state}\n"
                f"Approval mode: {context.approval_mode}\n"
                "Modes: all, inline, auto, deny"
            )
        if mode not in {"all", "inline", "auto", "deny"}:
            return CommandResult(False, "Usage: /approve <all, inline, auto, deny>")
        shell = "disabled" if mode == "deny" else "enabled"
        return _result(
            f"Shell is now {shell}; approval mode is {mode}.",
            f"approval-mode:{mode}",
        )

    def skills(context: CommandContext, arguments: str) -> CommandResult:
        del arguments
        names = ", ".join(context.skill_names) or "(none discovered)"
        active = context.active_skill or "(none)"
        return _result(
            f"Project Skills: {names}\n"
            f"Active Skill: {active}\n"
            "Use /skill <name> or /skill off."
        )

    def skill(context: CommandContext, arguments: str) -> CommandResult:
        name = arguments.strip().casefold()
        if not name:
            return CommandResult(False, "Usage: /skill <name> or /skill off")
        if name == "off":
            return _result("Disabled the active Skill.", "skill:off")
        if name not in context.skill_names:
            return CommandResult(False, f"Unknown Skill: {name}. Use /skills.")
        return _result(f"Activated Skill: {name}", f"skill:{name}")

    def mcp(context: CommandContext, arguments: str) -> CommandResult:
        del arguments
        servers = ", ".join(context.mcp_servers) or "(none configured)"
        return _result(f"MCP servers: {servers}\nMCP connection is added in a later increment.")

    def tools(context: CommandContext, arguments: str) -> CommandResult:
        del arguments
        names = "\n".join(f"- {name}" for name in context.tool_names) or "(none)"
        return _result(f"Available tools:\n{names}")

    register("help", "Show this command list.", help_command, aliases=("?",))
    register("status", "Show Session and runtime policy.", status)
    register(
        "new",
        "Create a new Session.",
        lambda context, arguments: _result("Created a new Session.", "new"),
        allowed_while_active=False,
    )
    register(
        "clear",
        "Clear visible cards only.",
        lambda context, arguments: _result("Cleared the current view.", "clear"),
    )
    register(
        "workspace",
        "Show the Session workspace.",
        lambda context, arguments: _result(str(context.workspace)),
    )
    register(
        "plan",
        "Toggle the plan panel.",
        lambda context, arguments: _result("Toggled the plan panel.", "plan"),
    )
    register(
        "approve",
        "Show or change Shell approval policy.",
        approve,
        usage="/approve [mode]",
        aliases=("approvals", "permissions"),
        allowed_while_active=False,
    )
    register("skills", "List project Skills.", skills)
    register("skill", "Activate a project Skill.", skill, usage="/skill <name|off>")
    register("mcp", "Show MCP server status.", mcp)
    register("tools", "List available tools.", tools)
    register(
        "exit",
        "Exit Miniclaude.",
        lambda context, arguments: _result("Exiting Miniclaude.", "exit"),
        aliases=("quit",),
        allowed_while_active=False,
    )
    return CommandRegistry(tuple(specs))
