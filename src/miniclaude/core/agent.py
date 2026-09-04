"""A deliberately visible ReAct loop: model -> tools -> observation -> model."""

import json
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Protocol

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from miniclaude.core.prompts import ACTOR_PROMPT
from miniclaude.core.state import RuntimeState
from miniclaude.providers.openai_provider import create_model
from miniclaude.tools.registry import build_tools, execute_tool


class ChatModel(Protocol):
    def bind_tools(self, tools): ...


def _text(content) -> str:
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


def stream_agent_events(
    task: str,
    *,
    workspace: Path,
    max_loops: int = 10,
    allow_shell: bool = False,
    model: ChatModel | None = None,
) -> Iterator[dict]:
    """Stream execution events; a final answer is not independent verification.

    Injecting a model keeps offline tests deterministic while all tools stay real.
    The CLI owns provider configuration and passes its configured model here.
    """
    if not task.strip() or not 1 <= max_loops <= 100:
        raise ValueError("task must be nonempty and max_loops must be between 1 and 100")
    model = create_model() if model is None else model
    state = RuntimeState(workspace=workspace, allow_shell=allow_shell)
    tools = build_tools(state)
    context = (
        f"Workspace: {state.workspace}\n"
        f"Shell: {'PowerShell' if os.name == 'nt' else 'sh'}; enabled={allow_shell}\n"
        "Python executable command: python\n"
    )
    messages = [SystemMessage(content=ACTOR_PROMPT + "\n" + context), HumanMessage(content=task)]
    yield {"type": "run_start", "workspace": str(state.workspace), "allow_shell": allow_shell}
    try:
        agent = model.bind_tools(tools)
    except Exception as exc:
        yield {
            "type": "error",
            "code": "model_setup",
            "message": f"Tool binding failed ({type(exc).__name__})",
        }
        return
    seen_call_ids = set()
    for iteration in range(1, max_loops + 1):
        yield {"type": "model_start", "iteration": iteration}
        try:
            response = agent.invoke(messages)
        except Exception as exc:
            # Provider exceptions may include request headers, URLs or credentials.
            yield {
                "type": "error",
                "code": "model_request",
                "message": (
                    f"Model request failed ({type(exc).__name__}); "
                    "check endpoint, model and credentials"
                ),
            }
            return
        if not isinstance(response, AIMessage) or response.invalid_tool_calls:
            yield {
                "type": "error",
                "code": "invalid_response",
                "message": "Model returned invalid tool calls or message",
            }
            return
        calls = response.tool_calls
        call_ids = [call.get("id") for call in calls]
        if (
            len(calls) > 32
            or any(not call_id for call_id in call_ids)
            or len(set(call_ids)) != len(call_ids)
            or seen_call_ids.intersection(call_ids)
        ):
            yield {
                "type": "error",
                "code": "invalid_response",
                "message": "Tool calls require unique IDs and at most 32 calls per turn",
            }
            return
        seen_call_ids.update(call_ids)
        content = _text(response.content)
        if content:
            yield {"type": "ai_message", "content": content}
        messages.append(response)
        if not calls:
            if not content.strip():
                yield {
                    "type": "error",
                    "code": "empty_answer",
                    "message": "Model returned no answer or tool calls",
                }
                return
            yield {"type": "final_answer", "content": content, "status": "completed"}
            return
        for call in calls:
            yield {
                "type": "tool_call",
                "name": call["name"],
                "args": call["args"],
                "id": call["id"],
            }
            result = execute_tool(tools, call["name"], call["args"])
            messages.append(
                ToolMessage(
                    content=json.dumps(result, ensure_ascii=False),
                    tool_call_id=call["id"],
                    name=call["name"],
                )
            )
            yield {"type": "tool_result", "name": call["name"], "id": call["id"], "result": result}
    yield {
        "type": "error",
        "code": "max_loops",
        "message": f"Stopped after {max_loops} model calls without a final answer",
    }
