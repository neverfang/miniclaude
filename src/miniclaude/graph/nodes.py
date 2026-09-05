"""Node contracts and deterministic helpers for the stage-two workflow."""

import json
import subprocess
from collections.abc import Callable
from copy import deepcopy
from typing import Protocol

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, ConfigDict, Field, field_validator

from miniclaude.core.agent import ChatModel, stream_agent_events
from miniclaude.core.state import RuntimeState, ToolError
from miniclaude.graph.state import (
    MiniclaudeGraphState,
    VerificationCheck,
    VerificationResult,
)
from miniclaude.prompts.stage2 import (
    FINAL_PROMPT,
    PLANNER_PROMPT,
    STAGE2_ACTOR_PROMPT,
    VERIFIER_PROMPT,
)
from miniclaude.tools.bash_tool import run_bash
from miniclaude.tools.registry import build_readonly_tools, build_tools, execute_tool
from miniclaude.tools.todo_tools import TodoTracker, build_todo_tools, build_todo_update_tool


class GraphNodeError(RuntimeError):
    """A safe graph-node failure suitable for showing to the user."""


class StructuredOutputModel(Protocol):
    def with_structured_output(self, schema: type[BaseModel], **kwargs): ...


class VerifierModel(ChatModel, StructuredOutputModel, Protocol):
    """A chat model that supports both tool inspection and structured verdicts."""


class PlanTodo(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    id: str = Field(min_length=1, max_length=80)
    content: str = Field(min_length=1, max_length=500)


class PlanOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    plan_summary: str = Field(min_length=1, max_length=4000)
    todos: list[PlanTodo] = Field(min_length=1, max_length=30)
    acceptance_criteria: list[str] = Field(min_length=1, max_length=30)
    verification_commands: list[str] = Field(max_length=10)

    @field_validator("acceptance_criteria", "verification_commands")
    @classmethod
    def reject_blank_items(cls, values: list[str], info) -> list[str]:
        normalized = [value.strip() for value in values]
        if any(not value for value in normalized):
            raise ValueError("items must not be blank")
        if info.field_name == "acceptance_criteria" and len(set(normalized)) != len(normalized):
            raise ValueError("acceptance criteria must be unique")
        return normalized


class VerdictCheck(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=200)
    passed: bool
    detail: str = Field(min_length=1, max_length=2000)


class VerdictOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    passed: bool
    reason: str = Field(min_length=1, max_length=4000)
    checks: list[VerdictCheck] = Field(min_length=1, max_length=30)
    recommended_next_instruction: str = Field(max_length=4000)


def _bounded_previous_evidence(state: MiniclaudeGraphState) -> str:
    evidence = json.dumps(
        {
            "last_error": state["last_error"],
            "verification_results": state["verification_results"],
        },
        ensure_ascii=False,
    )
    return evidence[: state["runtime"].max_output_chars]


def make_planner_node(model: StructuredOutputModel, emit: Callable[[dict], None] | None = None):
    """Create a planner node with an injected model and optional event sink."""
    # Tool/function calling is supported by more OpenAI-compatible endpoints than
    # OpenAI's newer json_schema response format, including typical DeepSeek gateways.
    structured = model.with_structured_output(PlanOutput, method="function_calling")

    def planner(state: MiniclaudeGraphState) -> dict:
        attempt = state["attempts"] + 1
        user_prompt = (
            f"Task:\n{state['task']}\n\n"
            f"Prepare plan for attempt {attempt}.\n"
            f"Shell enabled: {state['runtime'].allow_shell}. When false, use no commands and "
            "make the acceptance criteria verifiable with read-only inspection.\n"
            f"Previous evidence:\n{_bounded_previous_evidence(state)}"
        )
        try:
            raw = structured.invoke(
                [SystemMessage(content=PLANNER_PROMPT), HumanMessage(content=user_prompt)]
            )
            plan = raw if isinstance(raw, PlanOutput) else PlanOutput.model_validate(raw)
            tracker = TodoTracker()
            planned_todos = [
                {
                    "id": todo.id,
                    "content": todo.content,
                    "status": "in_progress" if index == 0 else "pending",
                    "note": "",
                }
                for index, todo in enumerate(plan.todos)
            ]
            publication = execute_tool(
                build_todo_tools(tracker), "TodoWriteTool", {"todos": planned_todos}
            )
            if not publication["ok"]:
                raise ToolError(publication["error"])
        except Exception as exc:
            raise GraphNodeError(
                f"Planner failed to produce a valid plan ({type(exc).__name__})"
            ) from None
        update = {
            "plan_summary": plan.plan_summary,
            "todos": tracker.snapshot(),
            "acceptance_criteria": [item.strip() for item in plan.acceptance_criteria],
            "verification_commands": [item.strip() for item in plan.verification_commands],
        }
        if emit is not None:
            emit({"type": "planner", "attempt": attempt, **update})
        return update

    return planner


def run_verification_commands(
    runtime: RuntimeState, commands: list[str]
) -> list[VerificationResult]:
    """Run a bounded list of user-authorized verification commands in the workspace."""
    if not commands:
        return []
    if len(commands) > 10:
        raise ValueError("Provide at most 10 verification commands")
    normalized = [command.strip() for command in commands]
    if any(not command for command in normalized):
        raise ValueError("Verification command must not be empty")

    results: list[VerificationResult] = []
    for command in normalized:
        try:
            result = run_bash(runtime, command)
        except ToolError:
            results.append(
                VerificationResult(
                    command=command,
                    ok=False,
                    exit_code=None,
                    stdout="",
                    stderr="Shell execution is disabled; rerun with --allow-shell",
                )
            )
            continue
        except (OSError, subprocess.SubprocessError) as exc:
            results.append(
                VerificationResult(
                    command=command,
                    ok=False,
                    exit_code=None,
                    stdout="",
                    stderr=f"Command execution failed ({type(exc).__name__})",
                )
            )
            continue
        results.append(
            VerificationResult(
                command=command,
                ok=bool(result["ok"]),
                exit_code=result["exit_code"],
                stdout=result["stdout"],
                stderr=result["stderr"],
            )
        )
    return results


def make_actor_node(
    model: ChatModel,
    *,
    max_loops: int = 10,
    emit: Callable[[dict], None] | None = None,
):
    """Create an Actor that reuses the stage-one ReAct engine and real tools."""

    def actor(state: MiniclaudeGraphState) -> dict:
        tracker = TodoTracker(state["todos"])
        tools = build_tools(state["runtime"])
        tools.append(build_todo_update_tool(tracker))
        task = json.dumps(
            {
                "user_task": state["task"],
                "plan_summary": state["plan_summary"],
                "todos": state["todos"],
                "acceptance_criteria": state["acceptance_criteria"],
                "previous_failure": state["last_error"],
            },
            ensure_ascii=False,
        )
        summary = ""
        actor_error = ""
        captured_messages = []
        for event in stream_agent_events(
            task,
            workspace=state["runtime"].workspace,
            max_loops=max_loops,
            model=model,
            runtime=state["runtime"],
            tools=tools,
            system_prompt=STAGE2_ACTOR_PROMPT,
            captured_messages=captured_messages,
        ):
            if emit is not None:
                emit({"type": "actor_event", "event": event})
            if event["type"] == "final_answer":
                summary = event["content"]
            elif event["type"] == "error":
                actor_error = event["message"]
        if not summary:
            summary = actor_error or "Actor ended without an implementation summary"
        update = {
            "todos": tracker.snapshot(),
            "last_actor_summary": summary,
            "last_error": actor_error,
            "messages": captured_messages,
        }
        if emit is not None:
            emit(
                {
                    "type": "actor",
                    "attempt": state["attempts"] + 1,
                    "summary": summary,
                    "ok": not actor_error,
                }
            )
        return update

    return actor


def make_verifier_node(
    model: VerifierModel,
    *,
    max_loops: int = 8,
    emit: Callable[[dict], None] | None = None,
):
    """Create a verifier with deterministic commands and read-only workspace tools."""
    structured = model.with_structured_output(VerdictOutput, method="function_calling")

    def verifier(state: MiniclaudeGraphState) -> dict:
        runtime = state["runtime"]
        results = run_verification_commands(runtime, state["verification_commands"])
        inspection_runtime = RuntimeState(
            workspace=runtime.workspace,
            allow_shell=False,
            max_file_bytes=runtime.max_file_bytes,
            max_output_chars=runtime.max_output_chars,
            command_timeout=runtime.command_timeout,
        )
        evidence = json.dumps(
            {
                "task": state["task"],
                "acceptance_criteria": state["acceptance_criteria"],
                "verification_results": results,
                "actor_summary_untrusted": state["last_actor_summary"],
            },
            ensure_ascii=False,
        )[: runtime.max_output_chars]
        inspection_summary = ""
        verifier_error = ""
        captured_messages = []
        for event in stream_agent_events(
            (
                "Inspect the workspace with the read-only tools as needed. Then return ONLY a JSON "
                "object with passed, reason, checks, and recommended_next_instruction.\n\n"
                f"Evidence:\n{evidence}"
            ),
            workspace=inspection_runtime.workspace,
            max_loops=max_loops,
            model=model,
            runtime=inspection_runtime,
            tools=build_readonly_tools(inspection_runtime),
            system_prompt=VERIFIER_PROMPT,
            captured_messages=captured_messages,
        ):
            if emit is not None:
                emit({"type": "verifier_event", "event": event})
            if event["type"] == "final_answer":
                inspection_summary = event["content"]
            elif event["type"] == "error":
                verifier_error = event["message"]
        try:
            if verifier_error:
                raise GraphNodeError(verifier_error)
            section_budget = max(1, runtime.max_output_chars // 2)
            raw_verdict = structured.invoke(
                [
                    SystemMessage(content=VERIFIER_PROMPT),
                    HumanMessage(
                        content=(
                            "Return the structured verdict. Untrusted command evidence:\n"
                            f"{evidence[:section_budget]}"
                        )
                    ),
                    HumanMessage(
                        content=(
                            "Untrusted workspace inspection summary:\n"
                            f"{inspection_summary[:section_budget]}"
                        )
                    ),
                ]
            )
            verdict = (
                raw_verdict
                if isinstance(raw_verdict, VerdictOutput)
                else VerdictOutput.model_validate(raw_verdict)
            )
            checks = [
                VerificationCheck(name=check.name, passed=check.passed, detail=check.detail)
                for check in verdict.checks
            ]
            commands_ok = all(result["ok"] for result in results)
            checks_ok = all(check["passed"] for check in checks)
            expected_checks = state["acceptance_criteria"]
            actual_check_names = [check["name"] for check in checks]
            coverage_ok = (
                len(actual_check_names) == len(expected_checks)
                and len(set(actual_check_names)) == len(actual_check_names)
                and set(actual_check_names) == set(expected_checks)
            )
            passed = (
                verdict.passed and commands_ok and checks_ok and coverage_ok and not verifier_error
            )
            reason = verdict.reason
            next_instruction = verdict.recommended_next_instruction
            if not commands_ok:
                passed = False
                reason = f"Verification command failed. {reason}"
                next_instruction = next_instruction or "Fix the failing verification command"
            elif not coverage_ok:
                passed = False
                reason = f"Verifier did not cover every acceptance criteria exactly once. {reason}"
                next_instruction = next_instruction or "Check every acceptance criterion"
            elif not checks_ok:
                passed = False
                reason = f"Verifier reported a failed check. {reason}"
                next_instruction = next_instruction or "Fix the failed acceptance check"
        except Exception as exc:
            passed = False
            reason = verifier_error or (
                f"Verifier failed to produce a valid verdict ({type(exc).__name__})"
            )
            next_instruction = "Inspect the verification evidence and return a valid verdict"
            checks = [VerificationCheck(name="verifier output", passed=False, detail=reason)]
        final_todos = deepcopy(state["todos"])
        if passed:
            for todo in final_todos:
                todo["status"] = "completed"
                if not todo["note"]:
                    todo["note"] = reason
        elif state["attempts"] + 1 >= state["max_attempts"]:
            for todo in final_todos:
                if todo["status"] != "completed":
                    todo["status"] = "blocked"
                    todo["note"] = reason
        update = {
            "todos": final_todos,
            "verification_results": results,
            "verification_checks": checks,
            "passed": passed,
            "verification_reason": reason,
            "last_error": "" if passed else f"{reason} Next: {next_instruction}".strip(),
            "attempts": state["attempts"] + 1,
            "messages": captured_messages,
        }
        if emit is not None:
            emit(
                {
                    "type": "verifier",
                    "attempt": update["attempts"],
                    "passed": passed,
                    "reason": reason,
                    "checks": checks,
                }
            )
        return update

    return verifier


def make_final_node(emit: Callable[[dict], None] | None = None):
    """Create the deterministic final formatter."""

    def final(state: MiniclaudeGraphState) -> dict:
        if state["passed"]:
            answer = FINAL_PROMPT["success"].format(
                attempts=state["attempts"], summary=state["last_actor_summary"]
            )
        else:
            answer = FINAL_PROMPT["failure"].format(
                attempts=state["attempts"], reason=state["last_error"]
            )
        if emit is not None:
            emit({"type": "final", "passed": state["passed"], "content": answer})
        return {"final_answer": answer}

    return final
