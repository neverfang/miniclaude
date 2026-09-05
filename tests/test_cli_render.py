from io import BytesIO, StringIO, TextIOWrapper

from rich.console import Console

from miniclaude.cli.render import render_event


def rendered(*events: dict, width: int = 100) -> str:
    output = StringIO()
    console = Console(file=output, force_terminal=False, color_system=None, width=width)
    for event in events:
        render_event(console, event)
    return output.getvalue()


def test_tool_call_and_result_are_separate_panels():
    output = rendered(
        {
            "type": "tool_call",
            "name": "FileWriteTool",
            "args": {"file_path": "hello.py", "content": "print('one')\nprint('two')"},
        },
        {
            "type": "tool_result",
            "name": "FileWriteTool",
            "result": {"ok": True, "path": "hello.py"},
        },
    )

    assert "Tool Call - FileWriteTool" in output
    assert "Tool Result - FileWriteTool" in output
    assert "print('one')" in output
    assert "print('two')" in output
    assert "print('one')\\nprint('two')" not in output
    assert output.index("Tool Call") < output.index("Tool Result")


def test_long_tool_output_is_truncated_with_original_length():
    output = rendered(
        {
            "type": "tool_result",
            "name": "ShellTool",
            "result": {"ok": True, "stdout": "x" * 2500, "stderr": ""},
        }
    )

    assert "display truncated - original 2500 characters" in output
    assert "x" * 2500 not in output


def test_untrusted_markup_is_rendered_literally():
    output = rendered({"type": "ai_message", "content": "[bold]literal[/bold]"})

    assert "[bold]literal[/bold]" in output


def test_stage_events_have_distinct_panels():
    output = rendered(
        {
            "type": "planner",
            "attempt": 1,
            "plan_summary": "Write tests first",
            "todos": [{"id": "tests", "content": "Write tests", "status": "in_progress"}],
            "acceptance_criteria": ["tests pass"],
            "verification_commands": ["pytest -q"],
        },
        {
            "type": "actor",
            "attempt": 1,
            "ok": True,
            "summary": "implemented",
            "todos": [],
        },
        {
            "type": "verifier",
            "attempt": 1,
            "passed": True,
            "reason": "checks pass",
            "checks": [{"name": "tests pass", "passed": True, "detail": "pytest passed"}],
            "results": [],
        },
        {"type": "final", "passed": True, "content": "verified"},
    )

    assert "[planner] - Attempt 1" in output
    assert "[actor] - Attempt 1" in output
    assert "[verifier] - Attempt 1 - PASSED" in output
    assert "[final] - SUCCESS" in output
    assert "Acceptance criteria" in output
    assert "Verification commands" in output
    assert "[ok] tests pass" in output


def test_failure_events_are_visually_explicit():
    output = rendered(
        {
            "type": "verifier",
            "attempt": 2,
            "passed": False,
            "reason": "tests failed",
            "checks": [{"name": "tests pass", "passed": False, "detail": "exit 1"}],
            "results": [],
        },
        {"type": "final", "passed": False, "content": "not verified"},
    )

    assert "[verifier] - Attempt 2 - FAILED" in output
    assert "[x] tests pass" in output
    assert "[final] - FAILURE" in output


def test_unknown_event_does_not_dump_payload():
    output = rendered({"type": "mystery", "secret": "do-not-print"})

    assert "Unsupported event: mystery" in output
    assert "do-not-print" not in output


def test_nested_rich_sections_render_content_instead_of_object_representations():
    output = rendered(
        {
            "type": "planner",
            "attempt": 1,
            "plan_summary": "Run checks",
            "todos": [{"content": "Write tests", "status": "in_progress"}],
            "acceptance_criteria": [],
            "verification_commands": [],
        },
        {
            "type": "verifier",
            "attempt": 1,
            "passed": True,
            "reason": "verified",
            "checks": [],
            "results": [
                {
                    "command": "pytest -q",
                    "ok": True,
                    "exit_code": 0,
                    "stdout": "2 passed\nin 0.10s",
                    "stderr": "",
                }
            ],
        },
    )

    assert "[~] Write tests" in output
    assert '"[~] Write tests"' not in output
    assert "pytest -q" in output
    assert "2 passed" in output
    assert "rich.console.Group object" not in output


def test_status_markers_render_on_windows_gbk_console():
    raw = BytesIO()
    stream = TextIOWrapper(raw, encoding="gbk")
    console = Console(file=stream, force_terminal=False, color_system=None, width=100)

    render_event(
        console,
        {
            "type": "planner",
            "attempt": 1,
            "plan_summary": "Plan",
            "todos": [
                {"content": "Done", "status": "completed"},
                {"content": "Working", "status": "in_progress"},
            ],
            "acceptance_criteria": ["passes"],
            "verification_commands": ["pytest -q"],
        },
    )
    stream.flush()

    output = raw.getvalue().decode("gbk")
    assert "[planner] - Attempt 1" in output
    assert "[ok] Done" in output
    assert "[~] Working" in output


def test_multiagent_events_render_as_distinct_panels():
    output = rendered(
        {
            "type": "supervisor",
            "attempt": 1,
            "plan_summary": "coordinate",
            "todos": [],
            "acceptance_criteria": ["done"],
            "verification_commands": [],
            "research_notes": "facts",
            "sources": [],
        },
        {
            "type": "handoff",
            "from_agent": "planner",
            "to_agent": "searchAgent",
            "instruction": "research",
            "result": "facts",
            "ok": True,
        },
        {"type": "search_agent", "summary": "facts", "ok": True},
        {"type": "code_agent", "summary": "implemented", "ok": True},
        {
            "type": "react_event",
            "role": "searchAgent",
            "event": {
                "type": "tool_call",
                "name": "WebSearchTool",
                "args": {"query": "official docs"},
            },
        },
    )
    assert "[supervisor] - Attempt 1" in output
    assert "Handoff - planner -> searchAgent" in output
    assert "[searchAgent] - COMPLETE" in output
    assert "[codeAgent] - COMPLETE" in output
    assert "Tool Call [searchAgent] - WebSearchTool" in output
