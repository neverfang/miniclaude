from pathlib import Path

from miniclaude.commands.registry import CommandContext, build_command_registry


def context() -> CommandContext:
    return CommandContext(
        session_id="abc123def456",
        workspace=Path("workspace").resolve(),
        status="completed",
        route="chat",
        turn_active=False,
        allow_shell=False,
        approval_mode="inline",
        skill_names=("review",),
        active_skill="",
    )


def test_skill_command_activates_discovered_skill():
    result = build_command_registry().execute("/skill review", context())

    assert result.ok is True
    assert result.action == "skill:review"


def test_skill_command_rejects_unknown_skill_and_supports_off():
    registry = build_command_registry()

    assert registry.execute("/skill missing", context()).ok is False
    assert registry.execute("/skill off", context()).action == "skill:off"
