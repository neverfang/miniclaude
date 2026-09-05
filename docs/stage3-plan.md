# Stage Three MultiAgent Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make stage three the default CLI workflow by adding a tool-driven Supervisor, isolated SearchAgent and CodeAgent specialists, structured handoffs, Tavily search, workspace Notepad, independent verification, and visible streaming events.

**Architecture:** Preserve the stage-two graph as a regression and learning artifact. Add a new `START -> supervisor -> verifier -> supervisor|final` graph; specialist agents run synchronously behind Supervisor tools and return their results into node-local accumulated state.

**Tech Stack:** Python 3.11+, LangGraph 1.x, LangChain Core/OpenAI, Tavily Python 0.8.x, Rich, Typer, pytest, Ruff.

**Repository constraint:** Work directly on the current `main` checkout. Do not modify README, do not read or commit real secrets, and do not commit or push until the user explicitly asks after final verification.

---

### Task 1: Stage-three typed state, prompts, and package metadata

**Files:**
- Modify: `src/miniclaude/graph/state.py`
- Create: `src/miniclaude/prompts/stage3.py`
- Modify: `src/miniclaude/__init__.py`
- Modify: `pyproject.toml`
- Modify: `.env.example`
- Modify: `uv.lock`
- Modify: `tests/test_graph_state.py`
- Modify: `tests/test_package.py`

- [ ] Add failing tests asserting `SourceItem`, `AgentHandoff`, and the five stage-three initial-state fields exist with independent empty collections.
- [ ] Add a failing package test expecting version `0.3.0` and the constrained Tavily dependency.
- [ ] Run `uv run --locked pytest tests/test_graph_state.py tests/test_package.py -q` and confirm the new assertions fail for missing contracts/version.
- [ ] Add these typed records and initial values:

```python
class SourceItem(TypedDict):
    title: str
    url: str
    content: str
    score: float | None


class AgentHandoff(TypedDict):
    from_agent: str
    to_agent: str
    instruction: str
    result: str
    ok: bool
```

Add `research_notes`, `sources`, `agent_handoffs`, `code_agent_summary`, and `supervisor_summary` to `MiniclaudeGraphState` and `initial_graph_state`.
- [ ] Create `stage3.py` with the approved `SUPERVISOR_PROMPT`, `SEARCH_AGENT_PROMPT`, `CODE_AGENT_PROMPT`, and `STAGE3_VERIFIER_PROMPT`; each prompt explicitly lists role boundaries, ordering, evidence, and completion rules.
- [ ] Set package version to `0.3.0`, add `tavily-python>=0.8,<1`, add commented `TAVILY_API_KEY=` guidance to `.env.example`, and run `uv lock`.
- [ ] Re-run the focused tests and confirm green.

### Task 2: Bounded Tavily WebSearchTool

**Files:**
- Create: `src/miniclaude/tools/web_search_tool.py`
- Create: `tests/test_web_search_tool.py`

- [ ] Write failing tests for `build_web_search_tool(env_file=None, client=None, max_output_chars=12000)` covering missing key, environment-over-file precedence, valid normalization, HTTP(S)-only URLs, URL dedupe, result/content bounds, malformed response, provider exception sanitization, and absence of API keys in errors.
- [ ] Run `uv run --locked pytest tests/test_web_search_tool.py -q` and confirm import failure.
- [ ] Implement key loading with `dotenv_values(path, interpolate=False)` and lazy client creation:

```python
class SearchClient(Protocol):
    def search(self, query: str, **kwargs) -> dict:
        raise NotImplementedError


def build_web_search_tool(
    *, env_file: Path | None = None, client: SearchClient | None = None,
    max_output_chars: int = 12_000,
) -> StructuredTool:
    def search(query: str, max_results: int = 5) -> dict:
        normalized_query = query.strip()
        if not normalized_query or not 1 <= max_results <= 10:
            raise ToolError("query must be nonempty and max_results must be between 1 and 10")
        active_client = client or _configured_client(env_file)
        try:
            response = active_client.search(
                normalized_query,
                max_results=max_results,
                include_answer=True,
                include_raw_content=False,
            )
        except Exception as exc:
            return {"ok": False, "error": f"Web search failed ({type(exc).__name__})"}
        return _normalize_response(normalized_query, response, max_output_chars)
    return StructuredTool.from_function(search, name="WebSearchTool")
```

Implement `_configured_client` and `_normalize_response` in the same module. The production code imports `TavilyClient` only when an actual configured call occurs. Request `include_answer=True`, `include_raw_content=False`, and at most ten results. Each excerpt is capped at 1,500 characters and total returned text at `max_output_chars`.
- [ ] Run the focused tests and confirm green.

### Task 3: Workspace-confined Notepad tools

**Files:**
- Create: `src/miniclaude/tools/notepad_tools.py`
- Create: `tests/test_notepad_tools.py`

- [ ] Write failing tests for missing-file read, append/read round trip, newline normalization, fixed `NOTEPAD.md` path, 64 KiB size bound, UTF-8 behavior, symlink/junction refusal through `workspace_path`, and tool names.
- [ ] Run `uv run --locked pytest tests/test_notepad_tools.py -q` and confirm import failure.
- [ ] Implement `build_notepad_tools(runtime, max_bytes=65_536)` returning `NotepadAppendTool` and `NotepadReadTool`. The model supplies only `note` to append and optional `limit` to read; it never supplies a path. Resolve `NOTEPAD.md` through `workspace_path`, reject NUL/empty/oversized notes, cap read output by `runtime.max_output_chars`, and surface only `ToolError` messages through the existing execution boundary.
- [ ] Run focused tests and confirm green.

### Task 4: Isolated SearchAgent and CodeAgent

**Files:**
- Create: `src/miniclaude/agents/__init__.py`
- Create: `src/miniclaude/agents/search_agent.py`
- Create: `src/miniclaude/agents/code_agent.py`
- Create: `tests/test_specialist_agents.py`

- [ ] Add deterministic scripted-model tests proving SearchAgent binds exactly `WebSearchTool`, performs multiple tool calls, deduplicates collected sources, captures messages/events, rejects invalid/empty model completion, and stops at its loop bound.
- [ ] Add CodeAgent tests proving its tool set contains File/Grep/Shell/Todo/Notepad but not WebSearchTool, research notes and URLs enter its HumanMessage, Todo updates propagate, Notepad can be used, and model/loop failures return `ok=False` without exception detail leakage.
- [ ] Run `uv run --locked pytest tests/test_specialist_agents.py -q` and confirm imports fail.
- [ ] Implement both specialists as thin wrappers around `stream_agent_events`:

```python
def run_search_agent(
    state: MiniclaudeGraphState, instruction: str, *, model: ChatModel,
    web_search_tool: StructuredTool, writer: Callable[[dict], None] | None = None,
    max_loops: int = 4,
) -> dict:
    return _run_search_loop(
        state, instruction, model=model, web_search_tool=web_search_tool,
        writer=writer, max_loops=max_loops,
    )


def run_code_agent(
    state: MiniclaudeGraphState, instruction: str, *, model: ChatModel,
    writer: Callable[[dict], None] | None = None, max_loops: int = 10,
) -> dict:
    return _run_code_loop(
        state, instruction, model=model, writer=writer, max_loops=max_loops,
    )
```

Implement `_run_search_loop` and `_run_code_loop` in their respective modules using `stream_agent_events`. SearchAgent collects queries and valid sources from emitted tool results. CodeAgent owns a `TodoTracker`, builds standard tools plus `TodoUpdateTool` and Notepad tools, and returns the updated Todo snapshot. Both return `{ok, summary, messages, tool_events}` plus role-specific data and forward nested events with role-specific event types.
- [ ] Run focused tests and all existing agent/tool tests until green.

### Task 5: Supervisor tools and ordering enforcement

**Files:**
- Create: `src/miniclaude/graph/supervisor.py`
- Create: `tests/test_supervisor.py`

- [ ] Add failing tests for a Supervisor accumulator that starts from defensive state copies, publishes a full plan, rejects both delegation tools before current-attempt TodoWrite, records every successful/failed handoff, merges bounded/deduplicated research, merges CodeAgent Todos/messages/summary, and never exposes direct File/Shell/WebSearch tools.
- [ ] Add a scripted Supervisor ReAct test with the exact call sequence TodoWrite -> CallSearchAgent -> CallCodeAgent -> final summary, and another retry test proving prior verifier evidence is present while the current attempt must republish its plan.
- [ ] Run `uv run --locked pytest tests/test_supervisor.py -q` and confirm import failure.
- [ ] Implement `SupervisorAccumulator` plus `build_supervisor_tools(state, accumulator, search_runner, code_runner, emit)`. Use a stage-three TodoWriteTool signature containing `plan_summary`, `todos`, `acceptance_criteria`, and `verification_commands`; validate at most ten non-empty acceptance criteria/commands and prohibit commands when shell is disabled.
- [ ] Implement `make_supervisor_node(model, web_search_tool, max_loops=10, search_runner=run_search_agent, code_runner=run_code_agent, emit=None)` with `stream_agent_events`, only the three Supervisor tools, bounded serialized context, captured messages, and a returned update containing all accumulated fields. A specialist failure sets `last_error`; no delegation or empty summary can be treated as successful execution.
- [ ] Run focused tests and confirm green.

### Task 6: Stage-three Verifier and LangGraph workflow

**Files:**
- Modify: `src/miniclaude/graph/nodes.py`
- Create: `src/miniclaude/graph/stage3_workflow.py`
- Create: `tests/test_stage3_workflow.py`
- Modify: `tests/test_workflow.py`

- [ ] Add failing tests proving the stage-three Verifier prompt contains sources/handoffs/Notepad evidence, its interactive tools are FileRead/Grep/NotepadRead only, deterministic commands remain separate, and criterion coverage rules remain exact.
- [ ] Add graph tests for `START -> supervisor -> verifier -> final`, verifier failure -> supervisor retry, max-attempt termination, research-before-code handoff order, and a coding-only path with no SearchAgent call.
- [ ] Run the new workflow tests and confirm failure for missing stage-three builder.
- [ ] Extend `make_verifier_node` through optional prompt, context-builder, and read-only-extra-tools parameters without changing stage-two defaults. Add a stage-three context builder that includes bounded sources, handoffs, specialist summaries, and Notepad content.
- [ ] Implement `build_stage3_workflow(supervisor_model, verifier_model, web_search_tool, supervisor_max_loops=10, verifier_max_loops=8)` with node names `supervisor`, `verifier`, and `final`, recursion limit compatible with bounded attempts, and graph name `miniclaude-stage-three`.
- [ ] Run stage-two and stage-three workflow tests together and confirm green.

### Task 7: Default core streaming and Rich MultiAgent UI

**Files:**
- Modify: `src/miniclaude/core/agent.py`
- Modify: `src/miniclaude/cli/app.py`
- Modify: `src/miniclaude/cli/render.py`
- Modify: `tests/test_agent.py`
- Modify: `tests/test_cli.py`
- Modify: `tests/test_cli_render.py`

- [ ] Add failing core tests asserting the default builder is stage three, the selected `env_file` reaches WebSearchTool configuration, custom supervisor/search/code events normalize to `supervisor`, `handoff`, `search_agent`, `code_agent`, and role-tagged `react_event`, and nested start/final messages are suppressed.
- [ ] Add failing renderer/CLI tests for separate Windows-safe Supervisor, Handoff, SearchAgent, and CodeAgent panels; verify long research output truncation, literal markup, GBK compatibility, existing verifier/final panels, exit codes, and sanitized failures.
- [ ] Run focused tests and confirm expected failures.
- [ ] Update `stream_workflow_events(task, workspace, max_loops=10, max_attempts=3, allow_shell=False, model=None, env_file=None, workflow=None)` to build stage three by default while retaining injected-workflow tests. Normalize graph updates/custom events without leaking raw provider failures.
- [ ] Pass `env_file` from CLI to the core entry point. Add Rich renderers with ASCII-safe titles and markers, e.g. `[supervisor]`, `Handoff - planner -> searchAgent`, `[searchAgent]`, and `[codeAgent]`.
- [ ] Run core, CLI, renderer, stage-two workflow, and stage-three workflow tests until green.

### Task 8: Documentation and opt-in live acceptance

**Files:**
- Create: `docs/stage3.md`
- Create: `tests/test_stage3_live.py`
- Modify: `tests/conftest.py`

- [ ] Add a failing collection/skip test for an explicit `--run-live-stage3` option and confirm the live case skips by default.
- [ ] Implement an opt-in subprocess test requiring `OPENAI_API_KEY`, `OPENAI_MODEL`, and `TAVILY_API_KEY`. It requests a finite HTML research page with at least two HTTP(S) citations, bounds the subprocess, verifies the file exists, and checks citation count without starting a server or GUI.
- [ ] Write `docs/stage3.md` explaining architecture, role permissions, Handoff flow, Tavily/DeepSeek configuration, CLI usage, offline tests, explicit live test, expected files, safety limitations, and recommended reading order. Do not modify README.
- [ ] Run the live test without its flag and confirm it skips cleanly. Do not run the paid live path unless the user separately authorizes it.

### Task 9: Final verification and review

- [ ] Run `uv run --locked pytest -q --tb=short`; all offline tests must pass and paid live cases must remain skipped.
- [ ] Run `uv run --locked ruff check .`, `uv run --locked ruff format --check .`, `uv run --locked miniclaude --help`, and `git diff --check`.
- [ ] Confirm `git diff --name-only -- README.md` is empty and `git ls-files .env '.miniclaude/**'` contains no secrets/runtime files.
- [ ] Request a read-only code/spec review against `项目篇规划.md` and `docs/stage3-design.md`; fix Critical/Important findings with regression tests first.
- [ ] Re-run the complete verification suite after any review fixes and report exact results, live-test deferrals, and uncommitted state.
