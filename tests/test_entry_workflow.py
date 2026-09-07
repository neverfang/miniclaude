from __future__ import annotations

import math

import pytest
from langchain_core.messages import AIMessage

from miniclaude.graph.entry_workflow import respond_chat, route_intent


class StructuredRunner:
    def __init__(self, reply):
        self.reply = reply

    def invoke(self, messages):
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


class StructuredSequenceModel:
    def __init__(self, replies):
        self.replies = list(replies)
        self.schemas = []

    def with_structured_output(self, schema, **kwargs):
        self.schemas.append((schema, kwargs))
        return StructuredRunner(self.replies.pop(0))


class FailingModel:
    def __init__(self, message):
        self.message = message

    def with_structured_output(self, schema, **kwargs):
        return StructuredRunner(RuntimeError(self.message))


class ChatModelSpy:
    def __init__(self, reply):
        self.reply = reply
        self.bind_tools_calls = 0
        self.inputs = []

    def bind_tools(self, tools):
        self.bind_tools_calls += 1
        return self

    def invoke(self, messages):
        self.inputs.append(messages)
        return self.reply


def test_router_accepts_confident_chat():
    model = StructuredSequenceModel(
        [{"route": "chat", "reason": "greeting", "confidence": 0.9}]
    )

    result = route_intent("你好", session_context="", model=model)

    assert result == {
        "route": "chat",
        "reason": "greeting",
        "confidence": 0.9,
    }


@pytest.mark.parametrize(
    "reply",
    [
        {"route": "chat", "reason": "uncertain", "confidence": 0.54},
        {"route": "unknown", "reason": "bad", "confidence": 1.0},
        {"route": "chat", "reason": "bad", "confidence": math.nan},
    ],
)
def test_router_falls_back_to_workflow(reply):
    result = route_intent(
        "继续",
        session_context="prior coding task",
        model=StructuredSequenceModel([reply]),
    )

    assert result["route"] == "workflow"
    assert result["confidence"] == 0.0


def test_router_provider_failure_falls_back_without_secret():
    result = route_intent(
        "hello",
        session_context="",
        model=FailingModel("api_key=secret"),
    )

    assert result["route"] == "workflow"
    assert "secret" not in result["reason"]


def test_chat_responder_never_binds_tools():
    model = ChatModelSpy(AIMessage(content="你好，我在。"))

    answer = respond_chat("你好", session_context="", model=model)

    assert answer == "你好，我在。"
    assert model.bind_tools_calls == 0
