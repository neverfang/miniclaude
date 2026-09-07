from __future__ import annotations

import asyncio

from textual.widgets import Input

from miniclaude.cli.tui.app import MiniclaudeTuiApp
from miniclaude.core.session import create_session


def fake_turn_stream(task, **kwargs):
    yield {"type": "session_status", "status": "running"}
    yield {
        "type": "planner",
        "todos": [{"id": "one", "content": "Build", "status": "in_progress"}],
    }
    yield {
        "type": "react_event",
        "role": "actor",
        "event": {
            "type": "tool_call",
            "tool": "FileWriteTool",
            "args": {"file_path": "app.py"},
        },
    }
    yield {
        "type": "react_event",
        "role": "actor",
        "event": {
            "type": "tool_result",
            "tool": "FileWriteTool",
            "result": {"ok": True, "path": "app.py"},
        },
    }
    yield {
        "type": "session_final",
        "turn": 1,
        "route": "workflow",
        "passed": True,
        "content": "done",
    }


async def submit(pilot, text):
    prompt = pilot.app.query_one("#prompt", Input)
    prompt.value = text
    prompt.focus()
    await pilot.press("enter")
    await pilot.pause()


def test_app_has_execution_column_and_session_sidebar(tmp_path):
    async def scenario():
        session = create_session(tmp_path)
        app = MiniclaudeTuiApp(
            session=session,
            startup_directory=tmp_path,
            turn_stream=fake_turn_stream,
        )
        async with app.run_test(size=(120, 40)):
            assert app.query_one("#plan")
            assert app.query_one("#event-stream")
            assert app.query_one("#conversation")
            assert app.query_one("#session-sidebar")
            assert app.query_one("#prompt")

    asyncio.run(scenario())


def test_tool_call_and_result_render_as_separate_cards(tmp_path):
    async def scenario():
        session = create_session(tmp_path)
        app = MiniclaudeTuiApp(
            session=session,
            startup_directory=tmp_path,
            turn_stream=fake_turn_stream,
        )
        async with app.run_test(size=(120, 40)) as pilot:
            await submit(pilot, "build")
            await pilot.pause()
            assert len(app.query(".tool-call-card")) == 1
            assert len(app.query(".tool-result-card")) == 1
            assert "done" in app.query_one("#conversation").render().plain

    asyncio.run(scenario())


def test_duplicate_submit_is_ignored_and_input_reenabled(tmp_path):
    calls = []

    def stream(task, **kwargs):
        calls.append(task)
        yield {
            "type": "session_final",
            "turn": 1,
            "route": "chat",
            "passed": True,
            "content": "answer",
        }

    async def scenario():
        session = create_session(tmp_path)
        app = MiniclaudeTuiApp(
            session=session,
            startup_directory=tmp_path,
            turn_stream=stream,
        )
        async with app.run_test() as pilot:
            prompt = app.query_one("#prompt", Input)
            prompt.value = "hello"
            app.submit_prompt()
            app.submit_prompt()
            await pilot.pause()
            await pilot.pause()
            assert calls == ["hello"]
            assert prompt.disabled is False

    asyncio.run(scenario())


def test_two_chat_turns_keep_every_user_and_model_message_visible(tmp_path):
    answers = iter(["first answer", "second answer"])

    def stream(task, **kwargs):
        yield {
            "type": "session_final",
            "turn": 1 if task == "first question" else 2,
            "route": "chat",
            "passed": True,
            "content": next(answers),
        }

    async def scenario():
        session = create_session(tmp_path)
        app = MiniclaudeTuiApp(
            session=session,
            startup_directory=tmp_path,
            turn_stream=stream,
        )
        async with app.run_test() as pilot:
            await submit(pilot, "first question")
            await pilot.pause()
            await submit(pilot, "second question")
            await pilot.pause()
            rendered = app.query_one("#conversation").render().plain
            assert "first question" in rendered
            assert "first answer" in rendered
            assert "second question" in rendered
            assert "second answer" in rendered
            conversation = app.query_one("#conversation")
            assert conversation.scroll_y == conversation.max_scroll_y

    asyncio.run(scenario())


def test_ctrl_s_collapses_plan_and_ctrl_l_clears_visuals(tmp_path):
    async def scenario():
        session = create_session(tmp_path)
        app = MiniclaudeTuiApp(
            session=session,
            startup_directory=tmp_path,
            turn_stream=fake_turn_stream,
        )
        async with app.run_test() as pilot:
            await submit(pilot, "build")
            await pilot.press("ctrl+s")
            assert app.query_one("#plan").has_class("collapsed")
            await pilot.press("ctrl+l")
            await pilot.pause()
            assert len(app.query(".event-card")) == 0
            assert session["turn_index"] == 0

    asyncio.run(scenario())
