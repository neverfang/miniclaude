"""Explicit opt-in paid acceptance for Stage 6 Session routing."""

import pytest
from langchain_core.tools import StructuredTool

from miniclaude.core.session import create_session, load_session
from miniclaude.core.session_controller import stream_session_turn
from miniclaude.graph.entry_workflow import respond_chat, route_intent
from miniclaude.graph.stage4_workflow import build_stage4_workflow
from miniclaude.providers.openai_provider import create_model

pytestmark = pytest.mark.live


def test_live_stage6_chat_and_workflow_routes(request, tmp_path):
    if not request.config.getoption("--run-live-stage6"):
        pytest.skip("Requires --run-live-stage6: paid DeepSeek Session routing")
    try:
        model = create_model(env_file=request.config.rootpath / ".env")
    except ValueError as exc:
        pytest.skip(f"Live configuration not available: {exc}")

    def no_search(query: str) -> dict:
        """Prevent Tavily access during the Stage 6 acceptance."""
        return {"ok": False, "error": "Web search is disabled in this acceptance test"}

    workflow = build_stage4_workflow(
        supervisor_model=model,
        verifier_model=model,
        web_search_tool=StructuredTool.from_function(
            no_search,
            name="WebSearchTool",
        ),
        context_counter=model,
        supervisor_max_loops=12,
        verifier_max_loops=8,
    )
    session = create_session(tmp_path)

    chat_events = list(
        stream_session_turn(
            "你好，只回复一句简短问候，不要使用工具。",
            session=session,
            startup_directory=tmp_path,
            model=model,
            router=route_intent,
            chat=respond_chat,
        )
    )
    assert chat_events[-1]["type"] == "session_final"
    assert chat_events[-1]["route"] == "chat"

    workflow_events = list(
        stream_session_turn(
            "创建 hello.py，只包含 print('hello')，不要搜索网络，然后检查文件内容。",
            session=session,
            startup_directory=tmp_path,
            model=model,
            workflow_options={
                "workflow": workflow,
                "allow_shell": False,
                "checkpoint_mode": "light",
                "trace_mode": "on",
                "max_attempts": 2,
                "max_loops": 12,
            },
        )
    )
    assert workflow_events[-1]["type"] == "session_final"
    assert workflow_events[-1]["route"] == "workflow"
    assert (session["workspace"] / "hello.py").is_file()
    loaded = load_session(tmp_path, session["session_id"])
    assert loaded["turn_index"] == 2
    assert [item["role"] for item in loaded["recent_turns"]] == [
        "user",
        "assistant",
        "user",
        "assistant",
    ]
