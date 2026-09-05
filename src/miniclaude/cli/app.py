"""Typer entry point; presentation stays outside the Agent loop."""

from pathlib import Path
from typing import Annotated
from uuid import uuid4

import typer
from rich.console import Console
from rich.text import Text

from miniclaude.cli.render import render_event
from miniclaude.core.agent import stream_workflow_events
from miniclaude.providers.openai_provider import create_model

app = typer.Typer(add_completion=False, pretty_exceptions_enable=False)


@app.command()
def main(
    task: Annotated[
        str, typer.Argument(help="Task for the stage-four context-engineered MultiAgent workflow.")
    ],
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
):
    """Run the stage-four Supervisor-Specialists-Context-Verify workflow."""
    console = Console(highlight=False)
    if not task.strip():
        console.print(Text("Task must not be empty", style="red"))
        raise typer.Exit(2)
    try:
        model = create_model(env_file=env_file)
    except ValueError as exc:
        console.print(Text(str(exc), style="red"))
        raise typer.Exit(2) from None
    except Exception as exc:
        console.print(Text(f"Model configuration failed ({type(exc).__name__})", style="red"))
        raise typer.Exit(2) from None
    workspace = workspace or Path.cwd() / ".miniclaude/workspaces" / uuid4().hex[:12]
    if allow_shell:
        console.print(
            Text("WARNING: Shell is enabled. The workspace is NOT a sandbox.", style="yellow")
        )
    failed = True
    saw_final = False
    try:
        for event in stream_workflow_events(
            task,
            workspace=workspace,
            max_loops=max_loops,
            max_attempts=max_attempts,
            allow_shell=allow_shell,
            model=model,
            env_file=env_file,
        ):
            render_event(console, event)
            if event["type"] == "final":
                saw_final = True
                failed = not event["passed"]
    except KeyboardInterrupt:
        console.print(Text("Interrupted. Existing workspace files were retained."))
        raise typer.Exit(130) from None
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
