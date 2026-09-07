from __future__ import annotations

import pytest

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
