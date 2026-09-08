from __future__ import annotations

import asyncio
import threading

from textual.widgets import Input

from miniclaude.cli.tui.app import AgentEventMessage, MiniclaudeTuiApp
from miniclaude.core.session import (
    append_assistant_turn,
    append_user_turn,
    create_session,
)


def conversation_text(app):
    return "\n".join(
        message.render().plain
        for message in app.query(".conversation-message")
    )


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


def test_continued_session_renders_persisted_conversation(tmp_path):
    async def scenario():
        session = create_session(tmp_path)
        turn = append_user_turn(session, "persisted question")
        append_assistant_turn(
            session,
            turn=turn,
            route="chat",
            content="persisted model answer",
        )
        app = MiniclaudeTuiApp(
            session=session,
            startup_directory=tmp_path,
            turn_stream=fake_turn_stream,
        )
        async with app.run_test() as pilot:
            await pilot.pause()
            assert len(app.query(".conversation-message")) == 2
            rendered = conversation_text(app)
            assert "persisted question" in rendered
            assert "persisted model answer" in rendered

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
            assert "done" in conversation_text(app)

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
            assert len(app.query(".conversation-message")) == 4
            rendered = conversation_text(app)
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


def test_slash_help_is_local_and_does_not_increment_session(tmp_path):
    def should_not_run(task, **kwargs):
        raise AssertionError("slash commands must not reach the model turn stream")

    async def scenario():
        session = create_session(tmp_path)
        app = MiniclaudeTuiApp(
            session=session,
            startup_directory=tmp_path,
            turn_stream=should_not_run,
        )
        async with app.run_test() as pilot:
            await submit(pilot, "/help")
            await pilot.pause()
            assert session["turn_index"] == 0
            rendered = "\n".join(card.render().plain for card in app.query(".command-card"))
            assert "/status" in rendered

    asyncio.run(scenario())


def test_slash_new_changes_session_without_model_turn(tmp_path):
    def should_not_run(task, **kwargs):
        raise AssertionError("slash commands must not reach the model turn stream")

    async def scenario():
        session = create_session(tmp_path)
        original_id = session["session_id"]
        app = MiniclaudeTuiApp(
            session=session,
            startup_directory=tmp_path,
            turn_stream=should_not_run,
        )
        async with app.run_test() as pilot:
            await submit(pilot, "/new")
            await pilot.pause()
            assert app.session["session_id"] != original_id
            assert app.session["turn_index"] == 0

    asyncio.run(scenario())


def test_slash_prefix_shows_bounded_suggestions(tmp_path):
    async def scenario():
        app = MiniclaudeTuiApp(
            session=create_session(tmp_path),
            startup_directory=tmp_path,
            turn_stream=fake_turn_stream,
        )
        async with app.run_test() as pilot:
            prompt = app.query_one("#prompt", Input)
            prompt.value = "/st"
            await pilot.pause()
            suggestions = app.query_one("#command-suggestions")
            assert suggestions.display is True
            assert "/status" in suggestions.render().plain

    asyncio.run(scenario())


def test_slash_approve_changes_only_live_runtime_policy(tmp_path):
    async def scenario():
        app = MiniclaudeTuiApp(
            session=create_session(tmp_path),
            startup_directory=tmp_path,
            turn_stream=fake_turn_stream,
            workflow_options={"allow_shell": False, "approval_mode": "inline"},
        )
        async with app.run_test() as pilot:
            await submit(pilot, "/approve all")
            assert app.workflow_options["allow_shell"] is True
            assert app.workflow_options["approval_mode"] == "all"
            sidebar = app.query_one("#session-sidebar").render().plain
            assert "shell       enabled" in sidebar
            assert "approval    all" in sidebar

            await submit(pilot, "/approve deny")
            assert app.workflow_options["allow_shell"] is False
            assert app.workflow_options["approval_mode"] == "deny"

    asyncio.run(scenario())


def test_escape_cancels_turn_and_immediately_recovers_prompt(tmp_path):
    started = threading.Event()
    release = threading.Event()

    def blocking_stream(task, *, cancellation, run_id, **kwargs):
        started.set()
        cancellation.wait(2)
        release.wait(1)
        yield {"type": "session_status", "status": "running"}

    async def scenario():
        app = MiniclaudeTuiApp(
            session=create_session(tmp_path),
            startup_directory=tmp_path,
            turn_stream=blocking_stream,
        )
        async with app.run_test() as pilot:
            await submit(pilot, "long task")
            assert await asyncio.to_thread(started.wait, 1)
            old_run_id = app._active_run_id
            await pilot.press("escape")
            await pilot.pause()

            prompt = app.query_one("#prompt", Input)
            assert app._turn_active is False
            assert prompt.disabled is False
            assert app.view_state.status == "idle"

            app.on_agent_event_message(
                AgentEventMessage(
                    {"type": "session_status", "status": "running"},
                    run_id=old_run_id,
                )
            )
            assert app.view_state.status == "idle"
            release.set()

    asyncio.run(scenario())


def test_ctrl_c_cancels_active_turn_and_repeated_cancel_is_safe(tmp_path):
    started = threading.Event()

    def blocking_stream(task, *, cancellation, **kwargs):
        started.set()
        cancellation.wait(2)
        yield {"type": "session_cancelled"}

    async def scenario():
        app = MiniclaudeTuiApp(
            session=create_session(tmp_path),
            startup_directory=tmp_path,
            turn_stream=blocking_stream,
        )
        async with app.run_test() as pilot:
            await submit(pilot, "long task")
            assert await asyncio.to_thread(started.wait, 1)
            await pilot.press("ctrl+c")
            app.action_cancel()
            await pilot.pause()

            assert app._turn_active is False
            assert app.view_state.status == "idle"

    asyncio.run(scenario())


def test_idle_escape_is_a_noop(tmp_path):
    async def scenario():
        app = MiniclaudeTuiApp(
            session=create_session(tmp_path),
            startup_directory=tmp_path,
            turn_stream=fake_turn_stream,
        )
        async with app.run_test() as pilot:
            await pilot.press("escape")
            await pilot.pause()
            assert app.query_one("#prompt", Input).disabled is False
            assert app.view_state.status == "idle"

    asyncio.run(scenario())


def test_tui_approval_handler_marks_itself_as_the_event_renderer(tmp_path):
    captured = {}

    def stream(task, *, workflow_options, cancellation, **kwargs):
        captured["handler"] = workflow_options["approval_handler"]
        cancellation.cancel("test complete")
        yield {"type": "session_cancelled"}

    async def scenario():
        app = MiniclaudeTuiApp(
            session=create_session(tmp_path),
            startup_directory=tmp_path,
            turn_stream=stream,
        )
        async with app.run_test() as pilot:
            await submit(pilot, "inspect approval handler")
            await pilot.pause()
            assert captured["handler"]._renders_approval_events is True

    asyncio.run(scenario())
