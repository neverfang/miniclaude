"""Tool-driven stage-three planner and specialist coordinator."""

import json
from collections.abc import Callable
from copy import deepcopy

from langchain_core.tools import StructuredTool

from miniclaude.agents.code_agent import run_code_agent
from miniclaude.agents.search_agent import run_search_agent
from miniclaude.core.agent import ChatModel, stream_agent_events
from miniclaude.core.state import ToolError
from miniclaude.graph.state import AgentHandoff, MiniclaudeGraphState, SourceItem
from miniclaude.prompts.stage3 import SUPERVISOR_PROMPT
from miniclaude.tools.todo_tools import TodoTracker


class SupervisorAccumulator:
    """Own defensive copies of all state that specialist tools may update."""

    def __init__(self, state: MiniclaudeGraphState):
        self.state = state
        self.plan_summary = str(state.get("plan_summary", ""))
        self.todos = deepcopy(state.get("todos", []))
        self.acceptance_criteria = deepcopy(state.get("acceptance_criteria", []))
        self.verification_commands = deepcopy(state.get("verification_commands", []))
        self.research_notes = str(state.get("research_notes", ""))
        self.sources: list[SourceItem] = deepcopy(state.get("sources", []))
        self.agent_handoffs: list[AgentHandoff] = deepcopy(state.get("agent_handoffs", []))
        self.code_agent_summary = str(state.get("code_agent_summary", ""))
        self.messages = []
        self.last_error = ""
        self.plan_published = False

    def snapshot(self) -> MiniclaudeGraphState:
        snapshot = dict(self.state)
        snapshot.update(
            plan_summary=self.plan_summary,
            todos=deepcopy(self.todos),
            acceptance_criteria=deepcopy(self.acceptance_criteria),
            verification_commands=deepcopy(self.verification_commands),
            research_notes=self.research_notes,
            sources=deepcopy(self.sources),
            agent_handoffs=deepcopy(self.agent_handoffs),
            code_agent_summary=self.code_agent_summary,
        )
        return snapshot

    def publish_plan(
        self,
        plan_summary: str,
        todos: list[dict],
        acceptance_criteria: list[str],
        verification_commands: list[str],
    ) -> dict:
        summary = plan_summary.strip()
        criteria = [str(item).strip() for item in acceptance_criteria]
        commands = [str(item).strip() for item in verification_commands]
        if not summary:
            raise ToolError("Plan summary must not be empty")
        if not 1 <= len(criteria) <= 10 or any(not item for item in criteria):
            raise ToolError("Acceptance criteria must contain 1 to 10 non-empty items")
        if len(set(criteria)) != len(criteria):
            raise ToolError("Acceptance criteria must be unique")
        if len(commands) > 10 or any(not item for item in commands):
            raise ToolError("Verification commands must contain at most 10 non-empty items")
        if commands and not self.state["runtime"].allow_shell:
            raise ToolError("Verification commands require --allow-shell")
        normalized = []
        for index, todo in enumerate(todos):
            normalized.append(
                {
                    "id": str(todo.get("id", "")).strip(),
                    "content": str(todo.get("content", "")).strip(),
                    "status": "in_progress" if index == 0 else "pending",
                    "note": "",
                }
            )
        tracker = TodoTracker()
        tracker.write(normalized)
        self.plan_summary = summary
        self.todos = tracker.snapshot()
        self.acceptance_criteria = criteria
        self.verification_commands = commands
        self.plan_published = True
        self.last_error = ""
        return {"ok": True, "count": len(self.todos)}

    def record_handoff(self, to_agent: str, instruction: str, result: str, ok: bool) -> None:
        self.agent_handoffs.append(
            AgentHandoff(
                from_agent="planner",
                to_agent=to_agent,
                instruction=instruction[:2000],
                result=result[:4000],
                ok=ok,
            )
        )


def build_supervisor_tools(
    state: MiniclaudeGraphState,
    accumulator: SupervisorAccumulator,
    *,
    model: ChatModel,
    web_search_tool: StructuredTool,
    search_runner: Callable = run_search_agent,
    code_runner: Callable = run_code_agent,
    emit: Callable[[dict], None] | None = None,
) -> list[StructuredTool]:
    """Expose only planning and specialist-delegation tools to the Supervisor."""

    def publish_plan(
        plan_summary: str,
        todos: list[dict],
        acceptance_criteria: list[str],
        verification_commands: list[str],
    ) -> dict:
        """Publish the complete plan before any delegation in the current attempt."""
        return accumulator.publish_plan(
            plan_summary, todos, acceptance_criteria, verification_commands
        )

    def call_search_agent(instruction: str) -> dict:
        """Delegate a bounded external research instruction to searchAgent."""
        if not accumulator.plan_published:
            raise ToolError("Call TodoWriteTool before delegating")
        instruction = instruction.strip()
        if not instruction:
            raise ToolError("SearchAgent instruction must not be empty")
        result = search_runner(
            accumulator.snapshot(),
            instruction,
            model=model,
            web_search_tool=web_search_tool,
            writer=emit,
        )
        ok = bool(result.get("ok"))
        summary = str(result.get("summary", "SearchAgent returned no summary"))
        seen = {item["url"].casefold() for item in accumulator.sources}
        for source in result.get("sources", []):
            url = str(source.get("url", "")).strip()
            if url and url.casefold() not in seen and len(accumulator.sources) < 20:
                seen.add(url.casefold())
                accumulator.sources.append(source)
        note = summary.strip()
        if note:
            joined = "\n\n".join(filter(None, [accumulator.research_notes, note]))
            accumulator.research_notes = joined[: state["runtime"].max_output_chars]
        accumulator.messages.extend(result.get("messages", []))
        accumulator.record_handoff("searchAgent", instruction, summary, ok)
        if not ok:
            accumulator.last_error = summary
        handoff = accumulator.agent_handoffs[-1]
        if emit is not None:
            emit({"type": "handoff", "handoff": handoff})
        return {"ok": ok, "summary": summary, "source_count": len(result.get("sources", []))}

    def call_code_agent(instruction: str) -> dict:
        """Delegate workspace implementation and tests to codeAgent."""
        if not accumulator.plan_published:
            raise ToolError("Call TodoWriteTool before delegating")
        instruction = instruction.strip()
        if not instruction:
            raise ToolError("CodeAgent instruction must not be empty")
        result = code_runner(accumulator.snapshot(), instruction, model=model, writer=emit)
        ok = bool(result.get("ok"))
        summary = str(result.get("summary", "CodeAgent returned no summary"))
        if result.get("todos"):
            accumulator.todos = deepcopy(result["todos"])
        accumulator.code_agent_summary = summary
        accumulator.messages.extend(result.get("messages", []))
        accumulator.record_handoff("codeAgent", instruction, summary, ok)
        if not ok:
            accumulator.last_error = summary
        handoff = accumulator.agent_handoffs[-1]
        if emit is not None:
            emit({"type": "handoff", "handoff": handoff})
        return {"ok": ok, "summary": summary}

    return [
        StructuredTool.from_function(publish_plan, name="TodoWriteTool"),
        StructuredTool.from_function(call_search_agent, name="CallSearchAgentTool"),
        StructuredTool.from_function(call_code_agent, name="CallCodeAgentTool"),
    ]


def make_supervisor_node(
    model: ChatModel,
    web_search_tool: StructuredTool,
    *,
    max_loops: int = 10,
    search_runner: Callable = run_search_agent,
    code_runner: Callable = run_code_agent,
    emit: Callable[[dict], None] | None = None,
):
    """Create the stage-three Supervisor ReAct node."""

    def supervisor(state: MiniclaudeGraphState) -> dict:
        accumulator = SupervisorAccumulator(state)
        tools = build_supervisor_tools(
            state,
            accumulator,
            model=model,
            web_search_tool=web_search_tool,
            search_runner=search_runner,
            code_runner=code_runner,
            emit=emit,
        )
        context = json.dumps(
            {
                "task": state["task"],
                "attempt": state["attempts"] + 1,
                "shell_enabled": state["runtime"].allow_shell,
                "previous_verifier_failure": state.get("last_error", ""),
                "existing_research": state.get("research_notes", "")[:3000],
                "previous_handoffs": state.get("agent_handoffs", [])[-6:],
            },
            ensure_ascii=False,
        )[: state["runtime"].max_output_chars]
        captured_messages = []
        summary = ""
        loop_error = ""
        for event in stream_agent_events(
            context,
            workspace=state["runtime"].workspace,
            runtime=state["runtime"],
            model=model,
            tools=tools,
            system_prompt=SUPERVISOR_PROMPT,
            max_loops=max_loops,
            captured_messages=captured_messages,
        ):
            if emit is not None:
                emit({"type": "supervisor_event", "event": event})
            if event["type"] == "final_answer":
                summary = event["content"]
            elif event["type"] == "error":
                loop_error = event["message"]
        if not accumulator.plan_published:
            loop_error = loop_error or "Supervisor did not publish a plan"
        if not accumulator.agent_handoffs:
            loop_error = loop_error or "Supervisor did not delegate any work"
        if not summary:
            loop_error = loop_error or "Supervisor ended without a summary"
        update = {
            "plan_summary": accumulator.plan_summary,
            "todos": accumulator.todos,
            "acceptance_criteria": accumulator.acceptance_criteria,
            "verification_commands": accumulator.verification_commands,
            "research_notes": accumulator.research_notes,
            "sources": accumulator.sources,
            "agent_handoffs": accumulator.agent_handoffs,
            "code_agent_summary": accumulator.code_agent_summary,
            "supervisor_summary": summary or loop_error,
            "last_actor_summary": accumulator.code_agent_summary or summary,
            "last_error": accumulator.last_error or loop_error,
            "messages": accumulator.messages + captured_messages,
        }
        if emit is not None:
            emit({"type": "supervisor", "attempt": state["attempts"] + 1, **update})
        return update

    return supervisor
