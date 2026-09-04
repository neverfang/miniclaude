"""Typer entry point; presentation stays outside the Agent loop."""

import json
from pathlib import Path
from typing import Annotated
from uuid import uuid4

import typer
from rich.console import Console
from rich.text import Text

from miniclaude.core.agent import stream_agent_events
from miniclaude.providers.openai_provider import create_model

app = typer.Typer(add_completion=False, pretty_exceptions_enable=False)


def _show(console: Console, event: dict):
    kind = event["type"]
    if kind == "run_start":
        console.print(Text(f"Workspace: {event['workspace']}"))
    elif kind == "model_start":
        console.print(Text(f"[model] iteration {event['iteration']}"))
    elif kind == "tool_call":
        args = dict(event["args"])
        for key in ("content", "old_text", "new_text"):
            if key in args:
                args[key] = f"<{len(str(args[key]))} characters>"
        console.print(Text(f"[tool] {event['name']} {json.dumps(args, ensure_ascii=False)[:1500]}"))
    elif kind == "tool_result":
        result = json.dumps(event["result"], ensure_ascii=False)
        console.print(Text(f"[result] {event['name']}: {result[:2000]}"))
        if len(result) > 2000:
            console.print(Text("[display truncated; the model received the bounded tool result]"))
    elif kind == "final_answer":
        console.print(Text("\n[answer] " + event["content"]))
    elif kind == "error":
        console.print(Text(f"[error:{event['code']}] {event['message']}", style="red"))


@app.command()
def main(
    task: Annotated[str, typer.Argument(help="Task for the stage-one ReAct agent.")],
    workspace: Annotated[
        Path | None, typer.Option("--workspace", "-w", help="Generated files go here.")
    ] = None,
    max_loops: Annotated[int, typer.Option(min=1, max=100, help="Maximum model calls.")] = 10,
    allow_shell: Annotated[
        bool, typer.Option(help="Allow unsandboxed local commands. Trusted tasks only.")
    ] = False,
    env_file: Annotated[
        Path | None, typer.Option(help="Explicit .env file; default is startup directory/.env.")
    ] = None,
):
    """Run a small coding agent. No task is run without an explicit task argument."""
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
    failed = False
    try:
        for event in stream_agent_events(
            task, workspace=workspace, max_loops=max_loops, allow_shell=allow_shell, model=model
        ):
            _show(console, event)
            failed |= event["type"] == "error"
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
    if failed:
        raise typer.Exit(1)
