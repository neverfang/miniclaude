# Stage Three MultiAgent Design

**Date:** 2026-09-05

## Goal

Evolve miniclaude from one general-purpose Actor into a tool-driven Supervisor coordinating two specialists: `searchAgent` for external research and `codeAgent` for implementation. Preserve independent verification, bounded retries, visible handoffs, and the educational stage-two implementation.

## Scope

Stage three adds:

- A Planner/Supervisor ReAct loop with `TodoWriteTool`, `CallSearchAgentTool`, and `CallCodeAgentTool`.
- A research-only SearchAgent using Tavily through `WebSearchTool`.
- An implementation-only CodeAgent using workspace, Todo, and Notepad tools.
- Structured handoff, research source, and specialist summary state.
- A stage-three workflow and normalized streaming events.
- Rich terminal panels for handoffs and specialist activity.
- Offline tests and an explicit opt-in DeepSeek + Tavily live acceptance test.

Stage two remains available as a separate workflow implementation and regression target. Stage four context compression and layered memory are not implemented; the Notepad boundary is introduced now because stage three explicitly requires durable CodeAgent notes.

README remains unchanged until the whole project is complete.

## Failure modes being addressed

- A single Actor mixes research and coding context and may use the wrong tools.
- Research can be skipped even when a task depends on current external facts.
- Specialist results can be lost between model turns or retries.
- A model may delegate before publishing a checkable plan.
- A specialist may overclaim completion without workspace evidence.
- Unbounded search results can flood model context or terminal output.
- Missing search credentials or provider failures can expose sensitive details.

## Architecture

```text
START
  |
  v
Planner / Supervisor ReAct
  |-- TodoWriteTool
  |-- CallSearchAgentTool --> searchAgent --> WebSearchTool
  `-- CallCodeAgentTool   --> codeAgent   --> File/Grep/Shell/Todo/Notepad
  |
  v
Verifier
  |-- deterministic verification commands
  |-- read-only workspace inspection
  `-- research-source checks when applicable
  |
  +-- passed ------------------------> Final --> END
  `-- failed and attempts remain ----> Planner
```

The Supervisor invokes specialists synchronously through tools. A specialist result returns to the same Supervisor conversation, allowing it to inspect the result and decide whether another handoff is needed. Graph state is updated only through the Supervisor node's returned update; tool closures mutate a node-local accumulator rather than mutating the input state directly.

## Files and responsibilities

- `src/miniclaude/agents/search_agent.py`: bounded SearchAgent ReAct loop and source collection.
- `src/miniclaude/agents/code_agent.py`: bounded CodeAgent ReAct loop using implementation tools.
- `src/miniclaude/tools/web_search_tool.py`: Tavily adapter, validation, output bounds, and injected-client test boundary.
- `src/miniclaude/tools/notepad_tools.py`: workspace-confined `NOTEPAD.md` append/read operations.
- `src/miniclaude/graph/supervisor.py`: Supervisor accumulator, delegation tools, ordering rules, and handoff records.
- `src/miniclaude/graph/stage3_workflow.py`: Supervisor -> Verifier -> Retry/Final graph.
- `src/miniclaude/prompts/stage3.py`: role-specific system prompts.
- `src/miniclaude/graph/state.py`: stage-three state fields and typed records.
- `src/miniclaude/core/agent.py`: stage-three workflow construction and normalized stream events.
- `src/miniclaude/cli/render.py`: handoff, SearchAgent, CodeAgent, and search-result presentation.

## State contracts

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


class MiniclaudeGraphState(TypedDict, total=False):
    research_notes: str
    sources: list[SourceItem]
    agent_handoffs: list[AgentHandoff]
    code_agent_summary: str
    supervisor_summary: str
```

Initial state supplies empty values for every stage-three field. Sources are deduplicated by normalized URL while preserving first-seen order. Research notes and handoff text are bounded before entering state.

## Supervisor contract

The Supervisor receives the user task, current Todo and acceptance state, previous verifier evidence, existing research notes, source URLs, and prior handoffs. It has no direct file, shell, or web tools.

Its tools are:

1. `TodoWriteTool(plan_summary, todos, acceptance_criteria, verification_commands)` publishes or revises the complete plan.
2. `CallSearchAgentTool(instruction)` invokes SearchAgent and merges bounded notes and sources.
3. `CallCodeAgentTool(instruction)` invokes CodeAgent and merges Todo changes, messages, durable-note summary, and implementation summary.

Delegation is rejected until TodoWriteTool succeeds in the current Supervisor attempt. If a task requires external research, the prompt requires SearchAgent before CodeAgent; this order is also represented in tests. The Supervisor must finish with a non-empty summary. Missing or failed specialist work makes the Supervisor node incomplete and gives the Verifier explicit failure evidence.

## SearchAgent contract

SearchAgent receives one focused instruction plus existing bounded research context. Its only tool is WebSearchTool. It may issue up to four model turns and a bounded number of queries per handoff.

WebSearchTool:

- Loads `TAVILY_API_KEY` from the environment or the same explicit `.env` selected by the CLI.
- Uses `tavily-python` through a lazily constructed or injected client.
- Accepts a non-empty query and a bounded result count.
- Returns `{ok, query, answer, results}` with `title`, `url`, `content`, and optional `score`.
- Requires HTTP(S) source URLs, deduplicates URLs, caps each content excerpt, and caps total output.
- Returns sanitized errors for missing credentials, timeouts, network errors, and malformed provider responses.

SearchAgent returns a non-empty summary, executed queries, deduplicated sources, captured messages, and tool events. It cannot access file, shell, Todo, or Notepad tools.

## CodeAgent contract

CodeAgent receives the user's task, a focused Supervisor instruction, plan/Todo state, previous verifier evidence, research notes, source URLs, and current Notepad content. It uses:

- FileReadTool, FileWriteTool, FileEditTool, GrepTool, and optionally BashTool.
- TodoUpdateTool bound to a node-local tracker.
- NotepadAppendTool and NotepadReadTool.

It cannot call WebSearchTool. It must update Todo status explicitly and return a non-empty summary. Existing stage-one ReAct safety rules continue to apply: workspace-relative paths, bounded files and output, optimistic read-before-write/edit checks, finite shell commands, and explicit `--allow-shell` authorization.

## Notepad contract

Notepad tools operate only on `<workspace>/NOTEPAD.md` through the existing workspace path protections. `NotepadAppendTool` appends one bounded UTF-8 note with a newline and refuses to exceed a configured maximum file size. `NotepadReadTool` returns bounded content. It never reads `.env`, Git metadata, or paths supplied by the model.

Notepad is a durable working artifact, not stage-four layered memory. No automatic context compression or summarization is added in stage three.

## Verifier contract

The stage-two deterministic verification-command runner remains authoritative. The stage-three Verifier receives specialist summaries, sources, handoffs, and Notepad context in addition to existing evidence. Its interactive tools remain non-mutating: FileReadTool, GrepTool, and NotepadReadTool. It does not run arbitrary shell through the model; only Planner-provided verification commands run through the existing explicit, bounded command runner when `--allow-shell` is enabled.

For researched deliverables, acceptance criteria must include source requirements. The Verifier checks actual output files and source URLs rather than trusting SearchAgent or CodeAgent summaries. Every acceptance criterion must still appear exactly once in the structured verdict.

## Workflow and retry behavior

`build_stage3_workflow` compiles:

```text
START -> supervisor -> verifier -> (supervisor | final) -> END
```

Each Verifier invocation increments `attempts`. On failure, previous reason, failed checks, command results, handoffs, and specialist summaries return to the Supervisor. The next attempt republishes the plan and delegates only the missing research or implementation fix. The graph terminates after `max_attempts` and uses the existing deterministic Final formatter.

The existing `build_workflow` stage-two graph remains unchanged for regression tests and learning comparisons. The CLI's default core entry point uses stage three after this feature lands.

## Streaming and terminal UI

Core streaming emits normalized events:

- `supervisor`
- `handoff`
- `search_agent`
- `code_agent`
- `react_event` with a role
- existing `verifier` and `final`

Nested specialist events are forwarded without duplicating nested run-start or final-answer events. The Rich renderer displays separate Windows-safe panels for Supervisor, each handoff, SearchAgent, CodeAgent, WebSearch calls/results, Verifier, and Final. Long content follows the existing display-only truncation rules; model-visible data is unchanged.

## Configuration and dependencies

- Package version becomes `0.3.0`.
- Add a constrained `tavily-python` runtime dependency.
- Add optional `TAVILY_API_KEY` to `.env.example` without reading or committing real secrets.
- The CLI passes the selected environment-file path to both model and Tavily configuration so one invocation has a consistent config source.

Missing Tavily configuration does not break non-research coding tasks. If the Supervisor delegates research without valid configuration, WebSearchTool returns a safe failure which propagates through the handoff and prevents a false successful verification.

## Testing strategy

All behavior is developed test-first with injected models and search clients:

1. WebSearchTool tests: valid response normalization, URL dedupe, bounds, missing key, provider exception, malformed response, and no secret leakage.
2. Notepad tests: append/read, workspace confinement, size bound, missing file, and fixed path.
3. SearchAgent tests: only WebSearchTool is bound, multiple queries collect sources, invalid model responses, loop bound, and captured events.
4. CodeAgent tests: no search tool, Todo transitions, file and Notepad tools, research context injection, error propagation, and loop bound.
5. Supervisor tests: Todo-before-delegation enforcement, handoff records, search/code state merge, failure handling, and retry evidence.
6. Workflow tests: no-search coding path, research-before-code path, successful verification, failure/retry, max-attempt stop, and stage-two regression.
7. Core/CLI tests: normalized events, separate Rich panels, GBK compatibility, exit codes, and sanitized failures.
8. Opt-in live test: real DeepSeek + Tavily produces an HTML research page with at least two valid source links and passes finite verification.

Default tests never call paid APIs or execute the live generated application.

## Acceptance criteria

- Planner acts as a Supervisor and has no direct file, shell, or web capabilities.
- SearchAgent binds only WebSearchTool; CodeAgent never receives WebSearchTool.
- Every specialist call produces an AgentHandoff record with instruction, result, and status.
- The Supervisor cannot delegate before publishing a plan in the current attempt.
- Research notes and sources reach CodeAgent and Verifier through typed state.
- CodeAgent can append/read bounded workspace Notepad content and update Todos.
- Verification remains independent, evidence-driven, and retry-bounded.
- Missing Tavily credentials and provider failures are safe and cannot produce false success.
- Stage-two workflow tests continue to pass while stage three becomes the default CLI flow.
- Terminal output clearly separates Supervisor, handoffs, specialists, tools, verification, and final status.
- The full offline suite, Ruff lint, Ruff format check, CLI help, and `git diff --check` pass.
- README and real secrets remain untouched; generated workspaces are not tracked.

## Explicitly deferred

- Stage-four context monitoring, compression, layered memory, and history summaries.
- Background or parallel specialist execution.
- General-purpose multi-provider web search.
- Persistent sessions beyond the workspace Notepad artifact.
