from langchain_core.messages import AIMessage, HumanMessage

from miniclaude.core.state import RuntimeState
from miniclaude.graph.stage3_workflow import build_stage3_workflow
from miniclaude.graph.state import initial_graph_state


class SequenceModel:
    def __init__(self, replies, structured_replies=None):
        self.replies = iter(replies)
        self.structured_replies = iter(structured_replies or [])
        self.inputs = []
        self.structured_inputs = []
        self.tool_names = []

    def bind_tools(self, tools):
        self.tool_names.append([tool.name for tool in tools])
        return self

    def invoke(self, messages):
        self.inputs.append(list(messages))
        return next(self.replies)

    def with_structured_output(self, schema, **kwargs):
        parent = self

        class Runner:
            def invoke(self, messages):
                parent.structured_inputs.append(list(messages))
                return next(parent.structured_replies)

        return Runner()


def call(name, args, ident):
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": ident}])


def plan_call(ident="p"):
    return call(
        "TodoWriteTool",
        {
            "plan_summary": "implement and verify",
            "todos": [{"id": "code", "content": "implement"}],
            "acceptance_criteria": ["result exists"],
            "verification_commands": [],
        },
        ident,
    )


def verdict(passed, reason):
    return {
        "passed": passed,
        "reason": reason,
        "checks": [{"name": "result exists", "passed": passed, "detail": reason}],
        "recommended_next_instruction": "repair" if not passed else "",
    }


def code_runner(snapshot, instruction, **kwargs):
    todos = snapshot["todos"]
    todos[0]["status"] = "completed"
    return {"ok": True, "summary": "implemented", "todos": todos, "messages": [], "tool_events": []}


def test_stage3_success_and_verifier_has_only_read_tools(tmp_path):
    (tmp_path / "NOTEPAD.md").write_text("durable decision", encoding="utf-8")
    supervisor = SequenceModel(
        [
            plan_call(),
            call("CallCodeAgentTool", {"instruction": "implement"}, "c"),
            AIMessage(content="done"),
        ]
    )
    verifier = SequenceModel([AIMessage(content="inspected")], [verdict(True, "good")])
    graph = build_stage3_workflow(
        supervisor_model=supervisor,
        verifier_model=verifier,
        web_search_tool=object(),
        code_runner=code_runner,
    )
    state = initial_graph_state("build", runtime=RuntimeState(tmp_path), max_attempts=1)

    result = graph.invoke(state)

    assert result["passed"] is True
    assert result["attempts"] == 1
    assert supervisor.tool_names[0] == ["TodoWriteTool", "CallSearchAgentTool", "CallCodeAgentTool"]
    assert set(verifier.tool_names[0]) == {"FileReadTool", "GrepTool", "NotepadReadTool"}
    context = "\n".join(
        str(message.content) for message in verifier.inputs[0] if isinstance(message, HumanMessage)
    )
    assert "durable decision" in context
    assert "codeAgent" in context


def test_stage3_retries_supervisor_after_failed_verification(tmp_path):
    supervisor = SequenceModel(
        [
            plan_call("p1"),
            call("CallCodeAgentTool", {"instruction": "first"}, "c1"),
            AIMessage(content="first"),
            plan_call("p2"),
            call("CallCodeAgentTool", {"instruction": "repair"}, "c2"),
            AIMessage(content="second"),
        ]
    )
    verifier = SequenceModel(
        [AIMessage(content="inspect 1"), AIMessage(content="inspect 2")],
        [verdict(False, "missing"), verdict(True, "fixed")],
    )
    graph = build_stage3_workflow(
        supervisor_model=supervisor,
        verifier_model=verifier,
        web_search_tool=object(),
        code_runner=code_runner,
    )
    state = initial_graph_state("build", runtime=RuntimeState(tmp_path), max_attempts=2)

    result = graph.invoke(state)

    assert result["passed"] is True
    assert result["attempts"] == 2
    retry_context = "\n".join(str(message.content) for message in supervisor.inputs[3])
    assert "missing" in retry_context


def test_stage3_stops_at_max_attempts(tmp_path):
    supervisor = SequenceModel(
        [
            plan_call(),
            call("CallCodeAgentTool", {"instruction": "implement"}, "c"),
            AIMessage(content="done"),
        ]
    )
    verifier = SequenceModel([AIMessage(content="inspect")], [verdict(False, "still broken")])
    graph = build_stage3_workflow(
        supervisor_model=supervisor,
        verifier_model=verifier,
        web_search_tool=object(),
        code_runner=code_runner,
    )
    state = initial_graph_state("build", runtime=RuntimeState(tmp_path), max_attempts=1)

    result = graph.invoke(state)

    assert result["passed"] is False
    assert result["attempts"] == 1
    assert "not verified" in result["final_answer"]


def test_stage3_cannot_pass_after_specialist_failure(tmp_path):
    supervisor = SequenceModel(
        [
            plan_call(),
            call("CallSearchAgentTool", {"instruction": "research"}, "s"),
            AIMessage(content="research failed"),
        ]
    )
    verifier = SequenceModel([AIMessage(content="inspect")], [verdict(True, "looks good")])

    def failed_search(snapshot, instruction, **kwargs):
        return {
            "ok": False,
            "summary": "missing TAVILY_API_KEY",
            "queries": [],
            "sources": [],
            "messages": [],
            "tool_events": [],
        }

    graph = build_stage3_workflow(
        supervisor_model=supervisor,
        verifier_model=verifier,
        web_search_tool=object(),
        search_runner=failed_search,
        code_runner=code_runner,
    )
    state = initial_graph_state("research", runtime=RuntimeState(tmp_path), max_attempts=1)

    result = graph.invoke(state)

    assert result["passed"] is False
    assert "upstream" in result["verification_reason"].casefold()
