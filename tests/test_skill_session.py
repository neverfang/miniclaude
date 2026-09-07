from miniclaude.core.session import create_session, load_session, save_session
from miniclaude.core.session_controller import stream_session_turn


def test_active_skill_is_persisted(tmp_path):
    session = create_session(tmp_path)
    session["active_skill"] = "review"
    save_session(tmp_path, session)

    restored = load_session(tmp_path, session["session_id"])

    assert restored["active_skill"] == "review"


def test_active_skill_is_injected_into_chat_context(tmp_path):
    skill = tmp_path / ".miniclaude" / "skills" / "review" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text("Always mention the review checklist.", encoding="utf-8")
    session = create_session(tmp_path)
    session["active_skill"] = "review"
    captured = {}

    def router(task, **kwargs):
        return {"route": "chat", "reason": "test"}

    def chat(task, *, session_context, model):
        captured["context"] = session_context
        return "answer"

    list(
        stream_session_turn(
            "hello",
            session=session,
            startup_directory=tmp_path,
            router=router,
            chat=chat,
        )
    )

    assert "Active project Skill: review" in captured["context"]
    assert "Always mention the review checklist." in captured["context"]
