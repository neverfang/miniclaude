"""Typer entry point; presentation stays outside the Agent loop."""

from pathlib import Path
from typing import Annotated
from uuid import uuid4

import typer
from rich.console import Console
from rich.prompt import Confirm
from rich.text import Text

from miniclaude.cli.render import render_event
from miniclaude.core.agent import stream_workflow_events
from miniclaude.core.approval import ApprovalDecision, ApprovalRequest
from miniclaude.core.snapshot import WorkspaceRestoreError
from miniclaude.providers.openai_provider import create_model

app = typer.Typer(add_completion=False, pretty_exceptions_enable=False)


def make_inline_approval_handler(console: Console):
    """Build a fail-closed interactive approval callback for risky commands."""

    def decide(request: ApprovalRequest) -> ApprovalDecision:
        requested = {
            "type": "approval_requested",
            "approval_id": request.id,
            "risk_level": request.risk_level,
            "risk_reason": request.risk_reason,
            "command": request.command,
        }
        render_event(console, requested)
        if not console.is_terminal:
            decision = ApprovalDecision(
                False, "Inline approval denied in a non-interactive terminal"
            )
        else:
            try:
                approved = Confirm.ask(
                    "Approve this exact command?", default=False, console=console
                )
            except Exception:
                decision = ApprovalDecision(False, "Inline approval input failed")
            else:
                decision = ApprovalDecision(
                    approved,
                    "Approved interactively" if approved else "Denied interactively",
                )
        render_event(
            console,
            {
                "type": "approval_resolved",
                "approval_id": request.id,
                "approved": decision.approved,
                "risk_reason": request.risk_reason,
            },
        )
        return decision

    decide._renders_approval_events = True

    return decide


@app.command()
def main(
    task: Annotated[str | None, typer.Argument(help="Task; omit when using --resume.")] = None,
    workspace: Annotated[
        Path | None, typer.Option("--workspace", "-w", help="Generated files go here.")
    ] = None,
    max_loops: Annotated[int, typer.Option(min=1, max=100, help="Maximum model calls.")] = 10,
    max_attempts: Annotated[
        int,
        typer.Option(min=1, max=10, help="Maximum Supervisor-Verify attempts."),
    ] = 3,
    allow_shell: Annotated[
        bool, typer.Option(help="Allow unsandboxed local commands. Trusted tasks only.")
    ] = False,
    env_file: Annotated[
        Path | None, typer.Option(help="Explicit .env file; default is startup directory/.env.")
    ] = None,
    approval_mode: Annotated[str, typer.Option(help="inline, auto, or deny.")] = "inline",
    checkpoint_mode: Annotated[str, typer.Option(help="light, strict, or off.")] = "light",
    trace_mode: Annotated[str, typer.Option(help="on or off.")] = "on",
    resume: Annotated[Path | None, typer.Option(help="Resume checkpoint workspace.")] = None,
    restore_workspace: Annotated[
        bool, typer.Option(help="Restore checkpoint files before resume.")
    ] = False,
):
    """Run the stage-four workflow with the stage-five execution harness."""
    console = Console(highlight=False)
    task_value = task or ""
    valid_modes = {
        "approval": (approval_mode, {"inline", "auto", "deny"}),
        "checkpoint": (checkpoint_mode, {"light", "strict", "off"}),
        "trace": (trace_mode, {"on", "off"}),
    }
    for label, (value, allowed) in valid_modes.items():
        if value not in allowed:
            choices = ", ".join(sorted(allowed))
            console.print(Text(f"Invalid {label} mode: {value}. Choose: {choices}.", style="red"))
            raise typer.Exit(2)
    if restore_workspace and resume is None:
        console.print(Text("--restore-workspace requires --resume", style="red"))
        raise typer.Exit(2)
    if resume is None and not task_value.strip():
        console.print(Text("Task must not be empty unless --resume is used", style="red"))
        raise typer.Exit(2)

    if resume is not None:
        selected_workspace = resume.resolve()
        if workspace is not None and workspace.resolve() != selected_workspace:
            console.print(Text("--workspace must match --resume", style="red"))
            raise typer.Exit(2)
    else:
        selected_workspace = (
            workspace.resolve()
            if workspace is not None
            else Path.cwd() / ".miniclaude/workspaces" / uuid4().hex[:12]
        )

    try:
        model = create_model(env_file=env_file)
    except ValueError as exc:
        console.print(Text(str(exc), style="red"))
        raise typer.Exit(2) from None
    except Exception as exc:
        console.print(Text(f"Model configuration failed ({type(exc).__name__})", style="red"))
        raise typer.Exit(2) from None

    if allow_shell:
        console.print(
            Text("WARNING: Shell is enabled. The workspace is NOT a sandbox.", style="yellow")
        )
    approval_handler = make_inline_approval_handler(console) if approval_mode == "inline" else None
    failed = True
    saw_final = False
    try:
        for event in stream_workflow_events(
            task_value,
            workspace=selected_workspace,
            max_loops=max_loops,
            max_attempts=max_attempts,
            allow_shell=allow_shell,
            model=model,
            env_file=env_file,
            approval_mode=approval_mode,
            approval_handler=approval_handler,
            checkpoint_mode=checkpoint_mode,
            trace_mode=trace_mode,
            resume_workspace=resume,
            restore_workspace=restore_workspace,
        ):
            render_event(console, event)
            if event["type"] == "final":
                saw_final = True
                failed = not event["passed"]
    except KeyboardInterrupt:
        console.print(Text("Interrupted. Existing workspace files were retained."))
        raise typer.Exit(130) from None
    except ValueError as exc:
        if resume is not None:
            console.print(
                Text(
                    f"Resume failed ({type(exc).__name__}); checkpoint was not used.",
                    style="red",
                )
            )
            raise typer.Exit(2) from None
        console.print(Text(f"Run failed ({type(exc).__name__})", style="red"))
        raise typer.Exit(1) from None
    except WorkspaceRestoreError as exc:
        console.print(
            Text(
                f"Resume restore failed; pre-restore backup snapshot: {exc.backup_commit}",
                style="red",
            )
        )
        raise typer.Exit(2) from None
    except Exception as exc:
        console.print(
            Text(
                f"Run failed ({type(exc).__name__}); inspect the workspace before retrying.",
                style="red",
            )
        )
        raise typer.Exit(1) from None
    if failed or not saw_final:
        raise typer.Exit(1)
