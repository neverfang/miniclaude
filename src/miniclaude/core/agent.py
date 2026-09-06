"""A deliberately visible ReAct loop: model -> tools -> observation -> model."""

import json
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Protocol

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import StructuredTool

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
    runtime: RuntimeState | None = None,
    tools: list[StructuredTool] | None = None,
    system_prompt: str = ACTOR_PROMPT,
    captured_messages: list[BaseMessage] | None = None,
) -> Iterator[dict]:
    """Stream execution events; a final answer is not independent verification.

    Injecting a model keeps offline tests deterministic while all tools stay real.
    The CLI owns provider configuration and passes its configured model here.
    """
    if not task.strip() or not 1 <= max_loops <= 100:
        raise ValueError("task must be nonempty and max_loops must be between 1 and 100")
    model = create_model() if model is None else model
    state = runtime or RuntimeState(workspace=workspace, allow_shell=allow_shell)
    if state.workspace != Path(workspace).resolve():
        raise ValueError("workspace and runtime.workspace must match")
    active_tools = build_tools(state) if tools is None else tools
    context = (
        f"Workspace: {state.workspace}\n"
        f"Shell: {'PowerShell' if os.name == 'nt' else 'sh'}; enabled={state.allow_shell}\n"
        "Python executable command: python\n"
    )
    messages = [SystemMessage(content=system_prompt + "\n" + context), HumanMessage(content=task)]
    if captured_messages is not None:
        captured_messages.extend(messages)
    yield {
        "type": "run_start",
        "workspace": str(state.workspace),
        "allow_shell": state.allow_shell,
    }
    try:
        agent = model.bind_tools(active_tools)
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
        if captured_messages is not None:
            captured_messages.append(response)
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
            result = execute_tool(active_tools, call["name"], call["args"])
            tool_message = ToolMessage(
                content=json.dumps(result, ensure_ascii=False),
                tool_call_id=call["id"],
                name=call["name"],
            )
            messages.append(tool_message)
            if captured_messages is not None:
                captured_messages.append(tool_message)
            yield {"type": "tool_result", "name": call["name"], "id": call["id"], "result": result}
    yield {
        "type": "error",
        "code": "max_loops",
        "message": f"Stopped after {max_loops} model calls without a final answer",
    }


def stream_workflow_events(
    task: str,
    *,
    workspace: Path,
    max_loops: int = 10,
    max_attempts: int = 3,
    allow_shell: bool = False,
    model: ChatModel | None = None,
    env_file: Path | None = None,
    workflow=None,
    approval_mode: str = "inline",
    approval_handler=None,
    checkpoint_mode: str = "light",
    trace_mode: str = "on",
    resume_workspace: Path | None = None,
    restore_workspace: bool = False,
    harness_factory=None,
) -> Iterator[dict]:
    """Build and stream the Stage 4 workflow through the Stage 5 harness."""
    if restore_workspace and resume_workspace is None:
        raise ValueError("restore_workspace requires resume_workspace")
    if (
        (not task.strip() and resume_workspace is None)
        or not 1 <= max_loops <= 100
        or not 1 <= max_attempts <= 10
    ):
        raise ValueError("invalid task, max_loops, or max_attempts")

    # Local imports avoid a module cycle because graph nodes reuse stream_agent_events.
    from langgraph.graph.message import add_messages

    from miniclaude.core.checkpoint import CheckpointManager
    from miniclaude.core.harness import HarnessRunner
    from miniclaude.graph.stage4_workflow import build_stage4_workflow
    from miniclaude.graph.state import initial_graph_state
    from miniclaude.tools.web_search_tool import build_web_search_tool

    configured_model = create_model(env_file=env_file) if model is None else model
    runtime = RuntimeState(
        workspace=workspace,
        allow_shell=allow_shell,
        approval_mode=approval_mode,
        approval_handler=approval_handler,
        checkpoint_mode=checkpoint_mode,
        trace_mode=trace_mode,
    )
    resume_event = None
    if resume_workspace is not None:
        if Path(resume_workspace).resolve() != runtime.workspace:
            raise ValueError("resume workspace and workspace must match")
        resume_manager = CheckpointManager(runtime, task=task)
        state, resume_event = resume_manager.load_resume_inputs(
            runtime, task=task or None, restore_workspace=restore_workspace
        )
    else:
        state = initial_graph_state(task, runtime=runtime, max_attempts=max_attempts)
    harness_type = HarnessRunner if harness_factory is None else harness_factory
    harness_options = {}
    if resume_event is not None and isinstance(resume_event.get("previous_trace_id"), str):
        harness_options["resumed_from_trace_id"] = resume_event["previous_trace_id"]
    harness = harness_type(runtime, str(state["task"]), **harness_options)
    compiled = workflow or build_stage4_workflow(
        supervisor_model=configured_model,
        verifier_model=configured_model,
        context_counter=configured_model,
        web_search_tool=build_web_search_tool(
            env_file=env_file, max_output_chars=runtime.max_output_chars
        ),
        supervisor_max_loops=max_loops,
    )

    yield {
        "type": "run_start",
        "workspace": str(runtime.workspace),
        "allow_shell": runtime.allow_shell,
    }
    yield from harness.start(state)
    if resume_event is not None:
        yield resume_event
        yield from harness.record_custom_event(resume_event, state)
    completed_attempts = int(state.get("attempts", 0))
    last_passed = False
    latest_node = "start"

    def harnessed_stream():
        try:
            yield from compiled.stream(
                state,
                stream_mode=["updates", "custom"],
                config={"recursion_limit": max_attempts * 6 + 12},
            )
        except KeyboardInterrupt:
            harness.interrupt(latest_node=latest_node, state=state)
            raise
        except BaseException:
            harness.fail(latest_node=latest_node, state=state)
            raise

    for mode, chunk in harnessed_stream():
        yield from harness.drain_runtime_events()
        if mode == "custom":
            kind = chunk.get("type")
            role_by_kind = {
                "supervisor_event": "supervisor",
                "search_agent_event": "searchAgent",
                "code_agent_event": "codeAgent",
                "actor_event": "actor",
                "verifier_event": "verifier",
            }
            trace_event = chunk
            nested_for_trace = chunk.get("event")
            if kind in role_by_kind and isinstance(nested_for_trace, dict):
                trace_event = {**nested_for_trace, "role": role_by_kind[kind]}
            harness_events = harness.record_custom_event(trace_event, state)
            if kind in role_by_kind:
                nested = chunk.get("event", {})
                if nested.get("type") not in {"run_start", "final_answer"}:
                    yield {
                        "type": "react_event",
                        "role": role_by_kind[kind],
                        "event": nested,
                    }
            elif kind == "handoff" and isinstance(chunk.get("handoff"), dict):
                handoff = chunk["handoff"]
                yield {"type": "handoff", **handoff}
                role_type = (
                    "search_agent" if handoff.get("to_agent") == "searchAgent" else "code_agent"
                )
                yield {
                    "type": role_type,
                    "summary": handoff.get("result", ""),
                    "ok": bool(handoff.get("ok")),
                }
            elif kind in {"context_monitor", "context_compressor"}:
                yield chunk
            yield from harness_events
            continue
        if mode != "updates" or not isinstance(chunk, dict):
            continue
        for node, update in chunk.items():
            if not isinstance(update, dict):
                continue
            latest_node = node
            merged_messages = None
            if "messages" in update:
                merged_messages = add_messages(state.get("messages", []), update["messages"])
            state.update(update)
            if merged_messages is not None:
                state["messages"] = merged_messages
            if node == "planner":
                yield {
                    "type": "planner",
                    "attempt": completed_attempts + 1,
                    "plan_summary": update.get("plan_summary", ""),
                    "todos": update.get("todos", []),
                    "acceptance_criteria": update.get("acceptance_criteria", []),
                    "verification_commands": update.get("verification_commands", []),
                }
            elif node in {"supervisor", "contextual_supervisor"}:
                yield {
                    "type": "supervisor",
                    "attempt": completed_attempts + 1,
                    "plan_summary": update.get("plan_summary", ""),
                    "todos": update.get("todos", []),
                    "acceptance_criteria": update.get("acceptance_criteria", []),
                    "verification_commands": update.get("verification_commands", []),
                    "research_notes": update.get("research_notes", ""),
                    "sources": update.get("sources", []),
                    "summary": update.get("supervisor_summary", ""),
                    "ok": not bool(update.get("last_error")),
                }
            elif node == "actor":
                yield {
                    "type": "actor",
                    "attempt": completed_attempts + 1,
                    "summary": update.get("last_actor_summary", ""),
                    "ok": not bool(update.get("last_error")),
                    "todos": update.get("todos", []),
                }
            elif node == "verifier":
                completed_attempts = update.get("attempts", completed_attempts + 1)
                last_passed = bool(update.get("passed"))
                yield {
                    "type": "verifier",
                    "attempt": completed_attempts,
                    "passed": last_passed,
                    "reason": update.get("verification_reason", update.get("last_error", "")),
                    "checks": update.get("verification_checks", []),
                    "results": update.get("verification_results", []),
                }
            elif node == "final":
                yield {
                    "type": "final",
                    "passed": last_passed,
                    "content": update.get("final_answer", ""),
                }
            yield from harness.record_graph_update(node, update, state)

    summary = harness.finish(
        status="passed" if last_passed else "failed",
        latest_node=latest_node,
        state=state,
    )
    yield from harness.drain_runtime_events()
    if summary is not None:
        yield summary
