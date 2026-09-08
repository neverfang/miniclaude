from __future__ import annotations

import pytest

from miniclaude.core.cancellation import CancellationToken
from miniclaude.core.session import create_session, load_session
from miniclaude.core.session_controller import stream_session_turn


def test_chat_turn_does_not_start_workflow(tmp_path):
    session = create_session(tmp_path)
    workflow_calls = []

    events = list(
        stream_session_turn(
            "你好",
            session=session,
            startup_directory=tmp_path,
            router=lambda *args, **kwargs: {
                "route": "chat",
                "reason": "greeting",
                "confidence": 1.0,
            },
            chat=lambda *args, **kwargs: "你好，我在。",
            workflow_stream=lambda *args, **kwargs: (
                workflow_calls.append(args) or iter(())
            ),
        )
    )

    assert workflow_calls == []
    assert events[-1]["type"] == "session_final"
    assert events[-1]["route"] == "chat"
    assert load_session(tmp_path, session["session_id"])["turn_index"] == 1


def test_workflow_turn_forwards_existing_events(tmp_path):
    session = create_session(tmp_path)
    source = iter(
        [
            {"type": "planner", "todos": []},
            {"type": "final", "passed": True, "content": "done"},
        ]
    )

    events = list(
        stream_session_turn(
            "build",
            session=session,
            startup_directory=tmp_path,
            router=lambda *args, **kwargs: {
                "route": "workflow",
                "reason": "work",
                "confidence": 1.0,
            },
            chat=lambda *args, **kwargs: pytest.fail("chat must not run"),
            workflow_stream=lambda *args, **kwargs: source,
        )
    )

    assert any(event["type"] == "planner" for event in events)
    assert events[-1]["type"] == "session_final"
    assert events[-1]["content"] == "done"


def test_failed_turn_is_persisted_without_provider_secret(tmp_path):
    session = create_session(tmp_path)

    events = list(
        stream_session_turn(
            "hello",
            session=session,
            startup_directory=tmp_path,
            router=lambda *args, **kwargs: {
                "route": "chat",
                "reason": "greeting",
                "confidence": 1.0,
            },
            chat=lambda *args, **kwargs: (_ for _ in ()).throw(
                RuntimeError("api_key=secret")
            ),
            workflow_stream=lambda *args, **kwargs: iter(()),
        )
    )

    assert events[-1]["type"] == "session_error"
    assert "secret" not in events[-1]["message"]
    loaded = load_session(tmp_path, session["session_id"])
    assert [item["role"] for item in loaded["recent_turns"]] == [
        "user",
        "assistant",
    ]


def test_final_save_failure_does_not_append_a_second_assistant(
    tmp_path,
    monkeypatch,
):
    import miniclaude.core.session_controller as controller

    session = create_session(tmp_path)
    real_save = controller.save_session
    calls = 0

    def fail_second_save(startup_directory, current):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("disk full")
        return real_save(startup_directory, current)

    monkeypatch.setattr(controller, "save_session", fail_second_save)

    events = list(
        stream_session_turn(
            "hello",
            session=session,
            startup_directory=tmp_path,
            router=lambda *args, **kwargs: {
                "route": "chat",
                "reason": "greeting",
                "confidence": 1.0,
            },
            chat=lambda *args, **kwargs: "answer",
            workflow_stream=lambda *args, **kwargs: iter(()),
        )
    )

    assert events[-1]["type"] == "session_error"
    assert [item["role"] for item in session["recent_turns"]] == [
        "user",
        "assistant",
    ]


def test_cancelled_turn_is_not_saved_as_assistant_and_next_turn_can_start(tmp_path):
    session = create_session(tmp_path)
    token = CancellationToken()

    def cancelled_router(*args, **kwargs):
        token.cancel("Ctrl+C pressed")
        return {"route": "chat", "reason": "chat", "confidence": 1.0}

    first = list(
        stream_session_turn(
            "first",
            session=session,
            startup_directory=tmp_path,
            run_id="run-one",
            cancellation=token,
            router=cancelled_router,
            chat=lambda *args, **kwargs: pytest.fail("chat must not run"),
        )
    )
    second = list(
        stream_session_turn(
            "second",
            session=session,
            startup_directory=tmp_path,
            run_id="run-two",
            cancellation=CancellationToken(),
            router=lambda *args, **kwargs: {
                "route": "chat",
                "reason": "chat",
                "confidence": 1.0,
            },
            chat=lambda *args, **kwargs: "answer",
        )
    )

    assert first[-1]["type"] == "session_cancelled"
    assert second[-1]["type"] == "session_final"
    assert [item["role"] for item in session["recent_turns"]] == [
        "user",
        "user",
        "assistant",
    ]
