"""Model-backed entry routing for Stage 6 Session turns."""

from __future__ import annotations

from typing import Literal, Protocol

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, ConfigDict, Field

from miniclaude.core.sanitize import sanitize_for_persistence
from miniclaude.prompts.stage6 import CHAT_RESPONDER_PROMPT, INTENT_ROUTER_PROMPT

_MAX_CHAT_RESPONSE = 8_000


class StructuredOutputModel(Protocol):
    def with_structured_output(self, schema: type[BaseModel], **kwargs): ...


class ChatModel(Protocol):
    def invoke(self, messages): ...


class IntentOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    route: Literal["chat", "workflow"]
    reason: str = Field(min_length=1, max_length=500)
    confidence: float = Field(ge=0.0, le=1.0, allow_inf_nan=False)


def route_intent(
    task: str,
    *,
    session_context: str,
    model: StructuredOutputModel,
) -> dict[str, object]:
    if not isinstance(task, str) or not task.strip():
        raise ValueError("task must not be blank")
    try:
        structured = model.with_structured_output(
            IntentOutput,
            method="function_calling",
        )
        prompt = (
            f"Session context:\n{session_context or '(none)'}\n\n"
            f"Current user turn:\n{task}"
        )
        raw = structured.invoke(
            [
                SystemMessage(content=INTENT_ROUTER_PROMPT),
                HumanMessage(content=prompt),
            ]
        )
        decision = (
            raw if isinstance(raw, IntentOutput) else IntentOutput.model_validate(raw)
        )
        if decision.confidence < 0.55:
            raise ValueError("low confidence")
        return decision.model_dump()
    except Exception as exc:
        return {
            "route": "workflow",
            "reason": f"router fallback ({type(exc).__name__})",
            "confidence": 0.0,
        }


def _text(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "\n".join(
            block["text"]
            for block in content
            if isinstance(block, dict)
            and block.get("type") == "text"
            and isinstance(block.get("text"), str)
        )
    return ""


def respond_chat(
    task: str,
    *,
    session_context: str,
    model: ChatModel,
) -> str:
    if not isinstance(task, str) or not task.strip():
        raise ValueError("task must not be blank")
    try:
        response = model.invoke(
            [
                SystemMessage(content=CHAT_RESPONDER_PROMPT),
                HumanMessage(
                    content=(
                        f"Session context:\n{session_context or '(none)'}\n\n"
                        f"Current user turn:\n{task}"
                    )
                ),
            ]
        )
        answer = _text(getattr(response, "content", response)).strip()
        if not answer:
            raise ValueError("empty response")
        sanitized = sanitize_for_persistence(answer)
        text = sanitized if isinstance(sanitized, str) else str(sanitized)
        return text[:_MAX_CHAT_RESPONSE]
    except Exception as exc:
        raise RuntimeError(
            f"Chat response failed ({type(exc).__name__})"
        ) from None
