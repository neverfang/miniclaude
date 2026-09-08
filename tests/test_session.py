from __future__ import annotations

import json

import pytest

from miniclaude.core.session import (
    MAX_RECENT_TURNS,
    MAX_SESSION_CONTEXT,
    MAX_TURN_CONTENT,
    SessionError,
    append_assistant_turn,
    append_user_turn,
    build_session_context,
    create_session,
    load_latest_session,
    load_session,
    mark_turn_cancelled,
    save_session,
)


def test_plain_creation_always_makes_a_new_isolated_session(tmp_path):
    first = create_session(tmp_path)
    second = create_session(tmp_path)

    assert first["session_id"] != second["session_id"]
    assert first["workspace"] != second["workspace"]
    assert first["workspace"].is_dir()
    assert second["workspace"].is_dir()


def test_save_and_load_latest_session_with_ordered_turns(tmp_path):
    session = create_session(tmp_path)
    turn = append_user_turn(session, "你好")
    append_assistant_turn(
        session,
        turn=turn,
        route="chat",
        content="你好，我在。",
    )
    save_session(tmp_path, session)

    loaded = load_latest_session(tmp_path)

    assert loaded["session_id"] == session["session_id"]
    assert loaded["turn_index"] == 1
    assert [item["role"] for item in loaded["recent_turns"]] == [
        "user",
        "assistant",
    ]
    summary = (
        tmp_path
        / ".miniclaude"
        / "sessions"
        / session["session_id"]
        / "SESSION_SUMMARY.md"
    ).read_text(encoding="utf-8")
    assert "你好" in summary
    assert "chat" in summary


def test_cancelled_user_turn_persists_without_assistant(tmp_path):
    session = create_session(tmp_path)
    turn = append_user_turn(session, "long request", run_id="run-one")

    assert mark_turn_cancelled(session, turn, "run-one", "Escape pressed") is True
    assert mark_turn_cancelled(session, turn, "run-one", "again") is False
    save_session(tmp_path, session)

    loaded = load_session(tmp_path, session["session_id"])
    assert loaded["recent_turns"][-1]["role"] == "user"
    assert loaded["recent_turns"][-1]["status"] == "cancelled"
    assert loaded["recent_turns"][-1]["run_id"] == "run-one"
    assert not any(item["role"] == "assistant" for item in loaded["recent_turns"])


def test_continue_without_history_is_an_actionable_error(tmp_path):
    with pytest.raises(SessionError, match="No previous session"):
        load_latest_session(tmp_path)


def test_explicit_session_rejects_path_shaped_identifier(tmp_path):
    with pytest.raises(SessionError, match="session id"):
        load_session(tmp_path, "../outside")


def test_invalid_session_is_not_overwritten(tmp_path):
    session = create_session(tmp_path)
    path = (
        tmp_path
        / ".miniclaude"
        / "sessions"
        / session["session_id"]
        / "session.json"
    )
    path.write_text('{"format_version":999}', encoding="utf-8")

    with pytest.raises(SessionError, match="version"):
        load_session(tmp_path, session["session_id"])

    assert path.read_text(encoding="utf-8") == '{"format_version":999}'


def test_turn_content_and_recent_history_are_bounded(tmp_path):
    session = create_session(tmp_path)
    for index in range(30):
        turn = append_user_turn(session, f"{index}:" + "x" * 5000)
        append_assistant_turn(
            session,
            turn=turn,
            route="chat",
            content="answer",
        )

    assert len(session["recent_turns"]) == MAX_RECENT_TURNS
    assert all(
        len(str(item["content"])) <= MAX_TURN_CONTENT
        for item in session["recent_turns"]
    )


def test_context_contains_recent_turns_and_safe_workspace_listing(tmp_path):
    session = create_session(tmp_path)
    (session["workspace"] / "app.py").write_text(
        "secret file body",
        encoding="utf-8",
    )
    (session["workspace"] / ".env").write_text(
        "API_KEY=secret",
        encoding="utf-8",
    )
    turn = append_user_turn(session, "创建应用")
    append_assistant_turn(
        session,
        turn=turn,
        route="workflow",
        content="完成",
        summary="创建 app.py",
    )

    context = build_session_context(session)

    assert "app.py" in context
    assert ".env" not in context
    assert "secret file body" not in context
    assert "创建 app.py" in context


def test_context_is_bounded_and_prefers_newer_turns(tmp_path):
    session = create_session(tmp_path)
    for index in range(20):
        turn = append_user_turn(session, f"old-{index}-" + "x" * 1000)
        append_assistant_turn(
            session,
            turn=turn,
            route="chat",
            content=f"answer-{index}",
        )

    context = build_session_context(session)

    assert len(context) <= MAX_SESSION_CONTEXT
    assert "answer-19" in context


@pytest.mark.parametrize(
    "mutation",
    [
        lambda data: data.update(created_at="not-a-timestamp"),
        lambda data: data.update(recent_turns=data["recent_turns"] * 11),
        lambda data: data["recent_turns"][1].update(turn=0),
    ],
)
def test_load_rejects_invalid_timestamp_bounds_and_turn_order(tmp_path, mutation):
    session = create_session(tmp_path)
    turn = append_user_turn(session, "hello")
    append_assistant_turn(
        session,
        turn=turn,
        route="chat",
        content="answer",
    )
    save_session(tmp_path, session)
    path = (
        tmp_path
        / ".miniclaude"
        / "sessions"
        / session["session_id"]
        / "session.json"
    )
    data = json.loads(path.read_text(encoding="utf-8"))
    mutation(data)
    path.write_text(json.dumps(data), encoding="utf-8")

    with pytest.raises(SessionError):
        load_session(tmp_path, session["session_id"])


def test_duplicate_assistant_turn_is_rejected(tmp_path):
    session = create_session(tmp_path)
    turn = append_user_turn(session, "hello")
    append_assistant_turn(
        session,
        turn=turn,
        route="chat",
        content="answer",
    )

    with pytest.raises(SessionError, match="already"):
        append_assistant_turn(
            session,
            turn=turn,
            route="chat",
            content="duplicate",
        )
