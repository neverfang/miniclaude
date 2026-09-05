from langchain_core.messages import AIMessage, HumanMessage

from miniclaude.core.state import RuntimeState
from miniclaude.graph.state import initial_graph_state
from miniclaude.graph.supervisor import (
    SupervisorAccumulator,
    build_supervisor_tools,
    make_supervisor_node,
)
from miniclaude.tools.registry import execute_tool


class SequenceModel:
    def __init__(self, replies):
        self.replies = iter(replies)
        self.tool_names = []
        self.inputs = []

    def bind_tools(self, tools):
        self.tool_names = [tool.name for tool in tools]
        return self

    def invoke(self, messages):
        self.inputs.append(list(messages))
        return next(self.replies)


def _plan_args():
    return {
        "plan_summary": "Research, implement, verify",
        "todos": [{"id": "build", "content": "Build the result"}],
        "acceptance_criteria": ["result exists"],
        "verification_commands": [],
    }


def test_supervisor_tools_enforce_plan_and_merge_handoffs(tmp_path):
    state = initial_graph_state("build", runtime=RuntimeState(tmp_path))
    acc = SupervisorAccumulator(state)

    def search_runner(snapshot, instruction, **kwargs):
        assert snapshot["todos"][0]["id"] == "build"
        return {
            "ok": True,
            "summary": "facts",
            "queries": ["q"],
            "sources": [
                {"title": "Docs", "url": "https://example.com", "content": "fact", "score": 1.0},
                {"title": "Dup", "url": "https://example.com", "content": "dup", "score": 0.5},
            ],
            "messages": [],
            "tool_events": [],
        }

    def code_runner(snapshot, instruction, **kwargs):
        todos = snapshot["todos"]
        todos[0]["status"] = "completed"
        return {"ok": True, "summary": "built", "todos": todos, "messages": [], "tool_events": []}

    tools = build_supervisor_tools(
        state,
        acc,
        model=object(),
        web_search_tool=object(),
        search_runner=search_runner,
        code_runner=code_runner,
    )
    assert [tool.name for tool in tools] == [
        "TodoWriteTool",
        "CallSearchAgentTool",
        "CallCodeAgentTool",
    ]
    assert execute_tool(tools, "CallCodeAgentTool", {"instruction": "build"})["ok"] is False
    assert execute_tool(tools, "TodoWriteTool", _plan_args())["ok"] is True
    assert execute_tool(tools, "CallSearchAgentTool", {"instruction": "research"})["ok"] is True
    assert execute_tool(tools, "CallCodeAgentTool", {"instruction": "build"})["ok"] is True
    assert len(acc.sources) == 1
    assert [handoff["to_agent"] for handoff in acc.agent_handoffs] == ["searchAgent", "codeAgent"]
    assert acc.todos[0]["status"] == "completed"
    assert acc.code_agent_summary == "built"


def test_supervisor_react_sequence_and_retry_context(tmp_path):
    calls = [
        {"name": "TodoWriteTool", "args": _plan_args(), "id": "p"},
        {"name": "CallSearchAgentTool", "args": {"instruction": "find facts"}, "id": "s"},
        {"name": "CallCodeAgentTool", "args": {"instruction": "implement"}, "id": "c"},
    ]
    model = SequenceModel(
        [AIMessage(content="", tool_calls=[call]) for call in calls] + [AIMessage(content="done")]
    )
    state = initial_graph_state("build", runtime=RuntimeState(tmp_path))
    state["attempts"] = 1
    state["last_error"] = "previous verifier failure"
    state["context_summary"] = "compressed supervisor history"
    state["memory_snapshot"] = {
        "rules": {"rules": ["verify claims"]},
        "working_memory": {"next_step": "implement"},
        "history_summary_store": {},
    }

    def search_runner(snapshot, instruction, **kwargs):
        return {
            "ok": True,
            "summary": "facts",
            "queries": [],
            "sources": [],
            "messages": [],
            "tool_events": [],
        }

    def code_runner(snapshot, instruction, **kwargs):
        return {
            "ok": True,
            "summary": "built",
            "todos": snapshot["todos"],
            "messages": [],
            "tool_events": [],
        }

    node = make_supervisor_node(
        model, object(), search_runner=search_runner, code_runner=code_runner
    )
    update = node(state)

    assert model.tool_names == ["TodoWriteTool", "CallSearchAgentTool", "CallCodeAgentTool"]
    human = next(message for message in model.inputs[0] if isinstance(message, HumanMessage))
    assert "previous verifier failure" in human.content
    assert "context_summary_untrusted" in human.content
    assert "compressed supervisor history" in human.content
    assert "memory_snapshot_untrusted" in human.content
    assert "verify claims" in human.content
    assert update["supervisor_summary"] == "done"
    assert len(update["agent_handoffs"]) == 2


def test_supervisor_rejects_commands_without_shell_and_records_failure(tmp_path):
    state = initial_graph_state("build", runtime=RuntimeState(tmp_path, allow_shell=False))
    acc = SupervisorAccumulator(state)
    tools = build_supervisor_tools(
        state,
        acc,
        model=object(),
        web_search_tool=object(),
        search_runner=lambda *args, **kwargs: {
            "ok": False,
            "summary": "provider unavailable",
            "sources": [],
            "messages": [],
        },
        code_runner=lambda *args, **kwargs: {},
    )
    invalid = {**_plan_args(), "verification_commands": ["pytest -q"]}
    assert execute_tool(tools, "TodoWriteTool", invalid)["ok"] is False
    assert execute_tool(tools, "TodoWriteTool", _plan_args())["ok"] is True
    result = execute_tool(tools, "CallSearchAgentTool", {"instruction": "research"})
    assert result["ok"] is False
    assert acc.agent_handoffs[-1]["ok"] is False
    assert acc.last_error == "provider unavailable"
