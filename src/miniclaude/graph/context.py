"""Token monitoring and context compression support for Stage 4."""

import json
import re
from collections.abc import Callable, Iterable
from typing import Any

from langchain_core.messages import BaseMessage

from miniclaude.graph.state import MiniclaudeGraphState

DEFAULT_CONTEXT_TOKEN_LIMIT = 400_000

_SECRET_ASSIGNMENT = re.compile(
    r"(?i)\b((?:OPENAI|DEEPSEEK|TAVILY)?_?API_KEY|AUTHORIZATION)"
    r"(\s*[:=]\s*)(?:[\"'']?)[^\s,\"''}]+"
)
_BEARER_TOKEN = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{4,}")
_OPENAI_STYLE_KEY = re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b")


def sanitize_text(value: object) -> str:
    """Remove common credential forms from text crossing a context boundary."""
    text = str(value or "")
    text = _SECRET_ASSIGNMENT.sub(r"\1\2[REDACTED]", text)
    text = _BEARER_TOKEN.sub("Bearer [REDACTED]", text)
    return _OPENAI_STYLE_KEY.sub("[REDACTED]", text)


def _sanitize_value(value: Any) -> Any:
    if isinstance(value, str):
        return sanitize_text(value)
    if isinstance(value, list):
        return [_sanitize_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_sanitize_value(item) for item in value)
    if isinstance(value, dict):
        return {key: _sanitize_value(item) for key, item in value.items()}
    return value


def sanitize_messages(messages: Iterable[BaseMessage]) -> list[BaseMessage]:
    """Copy messages with recursively sanitized content and metadata."""
    sanitized = []
    for message in messages:
        sanitized.append(
            message.model_copy(
                update={
                    "content": _sanitize_value(message.content),
                    "additional_kwargs": _sanitize_value(message.additional_kwargs),
                    "response_metadata": _sanitize_value(message.response_metadata),
                }
            )
        )
    return sanitized


def serialize_message_content(messages: Iterable[BaseMessage]) -> str:
    """Serialize mixed message content predictably for local estimation."""
    return json.dumps(
        [
            {
                "type": message.type,
                "content": message.content,
                "additional_kwargs": message.additional_kwargs,
            }
            for message in messages
        ],
        ensure_ascii=False,
        sort_keys=True,
        default=str,
    )


def estimate_context_tokens(
    messages: Iterable[BaseMessage],
    counter: object,
) -> tuple[int, str]:
    """Estimate tokens without making a provider request."""
    sanitized = sanitize_messages(messages)
    count_method = getattr(counter, "get_num_tokens_from_messages", None)
    if callable(count_method):
        try:
            return max(0, int(count_method(sanitized))), "model"
        except Exception:
            pass
    text = serialize_message_content(sanitized)
    return max(1, len(text) // 4), "fallback"


def make_context_monitor_node(
    counter: object,
    *,
    emit: Callable[[dict[str, object]], None] | None = None,
):
    """Create a fail-closed token monitor node."""

    def monitor(state: MiniclaudeGraphState) -> dict[str, object]:
        try:
            limit = int(state.get("context_token_limit", DEFAULT_CONTEXT_TOKEN_LIMIT))
            if limit < 1:
                raise ValueError("context token limit must be positive")
            count, method = estimate_context_tokens(state.get("messages", []), counter)
            final_selected = state.get("context_next_node") == "final" or bool(
                state.get("passed")
            )
            should_compress = count >= limit and not final_selected
            route = "final" if final_selected else ("compressor" if should_compress else "verifier")
            update: dict[str, object] = {
                "context_token_count": count,
                "context_token_limit": limit,
                "context_should_compress": should_compress,
                "context_next_node": route,
                "context_error": "",
                "context_count_method": method,
            }
            if emit is not None:
                emit(
                    {
                        "type": "context_monitor",
                        "tokens": count,
                        "limit": limit,
                        "method": method,
                        "route": route,
                    }
                )
            return update
        except Exception as exc:
            return {
                "context_should_compress": False,
                "context_next_node": "final",
                "context_error": f"Context monitoring failed ({type(exc).__name__})",
            }

    return monitor


def context_monitor_route(state: MiniclaudeGraphState) -> str:
    """Return a known Stage 4 destination, failing closed for invalid state."""
    route = state.get("context_next_node", "final")
    return route if route in {"verifier", "compressor", "final"} else "final"
