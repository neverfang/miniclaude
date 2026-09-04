import json

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from miniclaude.core.agent import stream_agent_events


class ScriptedModel:
    """Replace only the remote model; real tools and message classes remain in use."""

    def __init__(self, replies):
        self.replies = iter(replies)
        self.inputs = []

    def bind_tools(self, tools):
        return self

    def invoke(self, messages):
        self.inputs.append(list(messages))
        reply = next(self.replies)
        if isinstance(reply, Exception):
            raise reply
        return reply


def call(name, args, call_id="call-1"):
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": call_id}])


def test_end_to_end_model_creates_and_runs_real_code(tmp_path):
    model = ScriptedModel(
        [
            call("FileWriteTool", {"file_path": "add.py", "content": "print(2 + 3)\n"}),
            call("BashTool", {"command": "python add.py"}, "call-2"),
            AIMessage(content="Created add.py and ran it: 5."),
        ]
    )
    events = list(
        stream_agent_events(
            "Create and execute add.py", workspace=tmp_path, model=model, allow_shell=True
        )
    )
    assert (tmp_path / "add.py").read_text() == "print(2 + 3)\n"
    assert isinstance(model.inputs[0][0], SystemMessage)
    assert isinstance(model.inputs[0][1], HumanMessage)
    tool_message = model.inputs[2][-1]
    assert isinstance(tool_message, ToolMessage)
    assert tool_message.tool_call_id == "call-2"
    assert json.loads(tool_message.content)["stdout"].strip() == "5"
    assert events[-1]["type"] == "final_answer"
    assert events[-1]["status"] == "completed"


def test_multiple_calls_preserve_order_and_ids(tmp_path):
    response = AIMessage(
        content="",
        tool_calls=[
            {"name": "FileWriteTool", "args": {"file_path": "a", "content": "a"}, "id": "1"},
            {"name": "FileReadTool", "args": {"file_path": "a"}, "id": "2"},
        ],
    )
    model = ScriptedModel([response, AIMessage(content="done")])
    list(stream_agent_events("task", workspace=tmp_path, model=model))
    assert [m.tool_call_id for m in model.inputs[1] if isinstance(m, ToolMessage)] == ["1", "2"]
    assert json.loads(model.inputs[1][-1].content)["content"] == "1: a"


@pytest.mark.parametrize(
    "name,args", [("unknown", {}), ("FileReadTool", {}), ("FileReadTool", {"file_path": "missing"})]
)
def test_tool_errors_return_to_model_for_recovery(tmp_path, name, args):
    model = ScriptedModel([call(name, args), AIMessage(content="The tool failed.")])
    events = list(stream_agent_events("task", workspace=tmp_path, model=model))
    result = json.loads(model.inputs[1][-1].content)
    assert result["ok"] is False
    assert result["error"]
    assert events[-1]["type"] == "final_answer"


def test_loop_limit_is_not_success(tmp_path):
    model = ScriptedModel([call("FileReadTool", {"file_path": "missing"})])
    events = list(stream_agent_events("task", workspace=tmp_path, model=model, max_loops=1))
    assert events[-1]["type"] == "error"
    assert events[-1]["code"] == "max_loops"
    assert not any(e["type"] == "final_answer" for e in events)


@pytest.mark.parametrize(
    "reply",
    [
        AIMessage(content=""),
        AIMessage(
            content="",
            invalid_tool_calls=[
                {"name": "FileReadTool", "args": "{bad", "id": "1", "error": "invalid JSON"}
            ],
        ),
        AIMessage(content="", tool_calls=[{"name": "FileReadTool", "args": {}, "id": ""}]),
    ],
)
def test_invalid_model_response_fails_closed(tmp_path, reply):
    events = list(stream_agent_events("task", workspace=tmp_path, model=ScriptedModel([reply])))
    assert events[-1]["type"] == "error"
    assert not any(e["type"] == "tool_call" for e in events)


def test_model_failure_does_not_expose_raw_exception_or_claim_success(tmp_path):
    model = ScriptedModel([RuntimeError("Authorization: secret-value")])
    events = list(stream_agent_events("task", workspace=tmp_path, model=model))
    assert events[-1]["type"] == "error"
    assert "secret-value" not in json.dumps(events)


def test_end_to_end_all_tools_repair_a_failing_program(tmp_path):
    test_source = (
        "import unittest\nfrom add import add\n"
        "class AddTests(unittest.TestCase):\n"
        "    def test_positive(self): self.assertEqual(add(2, 3), 5)\n"
        "    def test_negative(self): self.assertEqual(add(-2, 2), 0)\n"
        "    def test_zero(self): self.assertEqual(add(0, 0), 0)\n"
    )
    model = ScriptedModel(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "FileWriteTool",
                        "id": "write-code",
                        "args": {
                            "file_path": "add.py",
                            "content": "def add(a, b):\n    return a - b\n",
                        },
                    },
                    {
                        "name": "FileWriteTool",
                        "id": "write-test",
                        "args": {"file_path": "test_add.py", "content": test_source},
                    },
                ],
            ),
            call("BashTool", {"command": "python -B -m unittest discover -v"}, "fail-test"),
            call("GrepTool", {"pattern": "return", "glob": "*.py"}, "search"),
            call("FileReadTool", {"file_path": "add.py"}, "read"),
            call(
                "FileEditTool",
                {"file_path": "add.py", "old_text": "a - b", "new_text": "a + b"},
                "edit",
            ),
            call("BashTool", {"command": "python -B -m unittest discover -v"}, "pass-test"),
            AIMessage(content="Fixed add.py; all three tests passed."),
        ]
    )
    events = list(
        stream_agent_events("Fix and test add", workspace=tmp_path, model=model, allow_shell=True)
    )
    results = {event["id"]: event["result"] for event in events if event["type"] == "tool_result"}
    assert results["fail-test"]["ok"] is False
    assert "FAILED" in results["fail-test"]["stderr"]
    assert results["edit"]["ok"]
    assert results["pass-test"]["ok"]
    assert "Ran 3 tests" in results["pass-test"]["stderr"]
    assert "OK" in results["pass-test"]["stderr"]
    assert events[-1]["type"] == "final_answer"


def test_block_content_extracts_text(tmp_path):
    model = ScriptedModel([AIMessage(content=[{"type": "text", "text": "Hello"}])])
    events = list(stream_agent_events("task", workspace=tmp_path, model=model))
    assert events[-1]["content"] == "Hello"


@pytest.mark.parametrize("task,loops", [("", 10), ("task", 0), ("task", -1)])
def test_invalid_loop_inputs(tmp_path, task, loops):
    with pytest.raises(ValueError):
        list(
            stream_agent_events(task, workspace=tmp_path, model=ScriptedModel([]), max_loops=loops)
        )
