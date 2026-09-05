# Stage Four Context Engineering Design

**Date:** 2026-09-05

## Goal

Add bounded context monitoring, automatic transcript compression, durable history summaries,
and layered memory to the Stage 3 MultiAgent workflow. Long-running tasks must remain resumable
without trusting raw chat history, while Stage 2 and Stage 3 remain intact as learning and
regression artifacts.

Stage 4 becomes the default CLI workflow. The context token limit is fixed at 400,000 tokens in
production, matching `项目篇规划.md`. Tests may inject a smaller state value to exercise routing
without constructing hundreds of thousands of tokens.

## Scope

Stage 4 adds:

- A deterministic three-layer memory snapshot.
- A Context Monitor node after each Supervisor round.
- Model-assisted structured compression with a deterministic fallback.
- `HISTORY_SUMMARY.md` as a fixed-path durable store.
- LangGraph message deletion through `RemoveMessage(REMOVE_ALL_MESSAGES)`.
- Context and compression events in the existing Rich terminal UI.
- Memory input for Supervisor, SearchAgent, CodeAgent, and Verifier.
- Offline coverage and an explicit opt-in real DeepSeek acceptance test.

Stage 4 does not add shell approval, checkpoints, sessions, intent routing, Textual TUI, parallel
specialists, or background execution. Those remain later-stage concerns. README remains unchanged
until the complete project is finished.

## Failure Modes

The design controls these concrete risks:

- Graph messages grow across specialist handoffs and verifier retries until a provider rejects the
  request.
- Removing messages loses completed work, important files, source URLs, or the latest repair
  instruction.
- Long tool output or repeated research dominates the next model input.
- A compression model returns malformed output or fails at the provider boundary.
- A new Supervisor round repeats completed work because it cannot recover prior state.
- History storage escapes the workspace, follows a linked file, grows without bound, or exposes
  configuration secrets.
- Compression repeatedly produces an over-limit result and creates an infinite graph loop.
- A compressed transcript is treated as proof of task completion instead of untrusted context.

## Architecture

Stage 2 and Stage 3 builders remain unchanged. A new Stage 4 graph compiles this flow:

```text
START
  |
  v
Contextual Supervisor
  |
  v
Context Monitor
  |-- token_count < 400,000 ----------------------> Verifier
  `-- token_count >= 400,000 --> Context Compressor
                                      |
                                      |-- compressed --> Supervisor
                                      `-- unrecoverable --> Final

Verifier
  |-- passed or max attempts reached --> Final --> END
  `-- failed and attempts remain ------> Supervisor
```

The Contextual Supervisor is a thin Stage 4 wrapper around the existing Stage 3 Supervisor. It
refreshes layered memory before delegation and passes that snapshot through the existing state
boundary. Specialist agents remain synchronous tools behind the Supervisor.

The Monitor runs after the full Supervisor round because that is when graph state contains the
new specialist transcripts, handoffs, research, and implementation summary. Existing per-tool
file and output limits keep one inner ReAct round bounded before the Monitor regains control.

## Files And Responsibilities

- `src/miniclaude/graph/memory.py`
  - Build Rules, Working Memory, and History Summary Store.
  - Bound text, sources, handoffs, todos, and compression history.
  - Format the snapshot for model prompts.
- `src/miniclaude/tools/history_tools.py`
  - Read and replace the fixed workspace `HISTORY_SUMMARY.md`.
  - Enforce UTF-8, regular-file, link, and size constraints.
- `src/miniclaude/prompts/stage4.py`
  - Define compression and recovery rules.
- `src/miniclaude/graph/context.py`
  - Token estimation, Context Monitor, structured compressor, deterministic fallback, and routes.
- `src/miniclaude/graph/stage4_workflow.py`
  - Compile the Stage 4 graph without changing the Stage 3 builder.
- `src/miniclaude/graph/state.py`
  - Add typed Memory and compression state.
- `src/miniclaude/agents/search_agent.py`
  - Accept bounded Stage 4 memory when present.
- `src/miniclaude/agents/code_agent.py`
  - Accept bounded Stage 4 memory when present.
- `src/miniclaude/graph/supervisor.py`
  - Include bounded memory and context summary when present.
- `src/miniclaude/graph/nodes.py`
  - Include Stage 4 evidence in the Verifier through existing optional hooks.
- `src/miniclaude/core/agent.py`
  - Build Stage 4 by default and normalize context events.
- `src/miniclaude/cli/render.py`
  - Render separate Monitor and Compressor panels.

## State Contracts

The graph state gains these records:

```python
class CompressionEvent(TypedDict):
    before_tokens: int
    after_tokens: int
    removed_messages: int
    attempt: int
    used_fallback: bool


class LayeredMemory(TypedDict):
    rules: dict
    working_memory: dict
    history_summary_store: dict
```

And these fields:

```python
context_summary: str
context_token_count: int
context_token_limit: int
context_should_compress: bool
context_next_node: str
context_error: str
compression_events: list[CompressionEvent]
memory_snapshot: LayeredMemory
history_summary: str
```

Initial state uses empty summaries, empty collections, no context error, token count zero, and a
production token limit of 400,000. Every initial-state call owns independent collections.

`context_next_node` is `verifier` after a Supervisor round. It remains explicit in state so the
monitor contract can later be reused at other graph boundaries without hidden routing knowledge.

## Three-Layer Memory

### Rules Layer

The Rules Layer is a copied constant assembled by the runtime. It includes:

- Work only inside the current workspace.
- Use workspace-relative paths.
- Todo state is the current execution plan.
- `NOTEPAD.md` contains durable specialist notes.
- `HISTORY_SUMMARY.md` contains runtime-managed compressed history.
- Agents cannot directly write layered memory or history summaries.
- Summaries and agent claims are context, not verification evidence.

### Working Memory

Working Memory represents current structured state rather than the raw transcript:

- node name and user task;
- plan summary, todos, acceptance criteria, and verification commands;
- bounded research notes and source title/URL pairs;
- the most recent six handoffs;
- bounded Supervisor, CodeAgent, and Verifier summaries;
- last error, attempts, and maximum attempts.

Text fields use explicit per-field caps. Sources never carry full Tavily excerpts into this layer.
All lists are defensively copied.

### History Summary Store

The third layer contains:

- bounded `HISTORY_SUMMARY.md` content;
- bounded `NOTEPAD.md` content;
- the latest in-memory context summary;
- the most recent three compression events.

The complete state may retain a bounded audit list of compression events, but only the last three
enter prompts.

## Durable History Store

`history_tools.py` is runtime infrastructure, not an LLM tool registry. Agents receive no method
that writes `HISTORY_SUMMARY.md`.

The store always resolves `HISTORY_SUMMARY.md` through the existing workspace path guard. It:

- accepts and returns UTF-8 text only;
- rejects NUL bytes, symlinks, junction escapes, non-regular files, and hard-linked files;
- caps stored content at 64 KiB;
- writes the latest cumulative summary rather than appending forever;
- replaces through a workspace-local temporary file and `os.replace` when supported;
- removes only its own validated temporary file after a failed replace;
- returns sanitized errors without absolute paths or source content.

A missing history file is a successful empty read. A persistence failure does not discard the
in-memory summary; it is emitted as a warning and recorded in the compression event.

## Contextual Agent Inputs

Stage 3 behavior remains valid when memory fields are absent. Stage 4 adds bounded fields:

- Supervisor receives `context_summary` and `memory_snapshot` before publishing its new plan.
- SearchAgent receives prior research-related memory and source URLs, but still binds only
  `WebSearchTool`.
- CodeAgent receives plan, durable notes, important files, completed work, and the next repair
  step, but still cannot call WebSearchTool.
- Verifier receives layered memory as untrusted context and continues to judge actual files,
  deterministic command results, and exact acceptance-criterion coverage.

Compression never grants a role additional tools and never weakens the Stage 3 failure-closed
rules.

## Token Estimation And Context Monitor

The Monitor builds a fresh memory snapshot, then counts the graph messages plus one serialized
memory payload.

Primary counting uses the already configured model's `get_num_tokens_from_messages`. This is a
local tokenizer operation and must not make a provider request. If it is missing, rejects the
DeepSeek model name, returns an invalid value, or raises, the deterministic fallback serializes the
same bounded payload and uses `max(1, len(text) // 4)`.

Production compression triggers when:

```python
context_token_count >= context_token_limit
```

The production initial value is exactly 400,000. Tests may construct state with a lower value. The
Monitor emits the count, limit, decision, counting method, and next node, but never dumps Memory or
messages to the terminal.

## Context Compressor

The configured DeepSeek-compatible model is injected into the compressor; it is not recreated
from ambient configuration. Structured output uses function-calling mode for the same compatibility
reason as Stage 2 planning and verification.

The compression schema contains:

```python
summary: str
active_goal: str
completed_work: list[str]
open_todos: list[str]
important_files: list[str]
tool_findings: list[str]
sources: list[str]
next_steps: list[str]
risks: list[str]
```

Every list and string is bounded and blank entries are rejected. The formatted summary is a plain,
stable recovery artifact with labeled sections. It contains source URLs but no raw API keys,
environment contents, authorization headers, or unbounded command output.

On valid structured output, the compressor:

1. formats a cumulative recovery summary;
2. writes the same summary to `HISTORY_SUMMARY.md`;
3. returns `RemoveMessage(id=REMOVE_ALL_MESSAGES)` followed by one `AIMessage` summary;
4. trims research notes, handoffs, and other long state fields;
5. recounts the replacement context;
6. appends a `CompressionEvent`;
7. routes to Supervisor.

## Deterministic Compression Fallback

Provider failure, invalid structured output, or an empty summary invokes a rule-based fallback. It
extracts only state already present:

- task and active plan;
- Todo statuses and notes;
- completed specialist summaries;
- important source title/URL pairs;
- recent handoffs;
- latest verification results and repair instruction;
- existing Notepad and history summaries.

The fallback is cumulative and bounded. It performs the same message replacement and persistence
steps and marks `used_fallback=True`.

If the first replacement is still at or above 400,000 tokens or does not reduce the count, the
runtime creates one stricter minimal recovery summary. If the minimal summary remains over the
limit, or three compression cycles occur without reaching Verifier, the graph sets a sanitized
`context_error` and routes to deterministic Final failure. It never loops indefinitely.

## Verification Semantics

Compression is not evidence that work is correct. The Stage 4 Verifier keeps the Stage 3 contract:

- deterministic verification commands are run only with explicit shell authorization;
- model-driven inspection has FileRead, Grep, and NotepadRead only;
- upstream specialist failure prevents a passed result;
- every acceptance criterion must be checked exactly once;
- summaries, Memory, history, and handoffs are labeled untrusted.

The Verifier may use history to find relevant files or decisions, but must inspect the current
workspace before passing.

## Streaming And Terminal UI

Core adds normalized events:

- `context_monitor`
- `context_compressor`
- existing `supervisor`, `handoff`, specialist, `verifier`, and `final` events

The Rich UI displays separate Windows-safe panels:

```text
[context-monitor]
tokens: 385421 / 400000
status: COMPRESSION REQUIRED
method: model | fallback

[context-compressor]
before: 385421
after: 52138
removed messages: 47
fallback: no
history persisted: yes
```

The UI never renders the full memory snapshot or history file automatically. Long summaries use
the existing display-only truncation and untrusted Rich markup remains literal.

## Error Handling

- Tokenizer errors use deterministic estimation and do not fail the run.
- Compressor provider and schema errors use deterministic fallback.
- History read errors produce empty durable history plus a sanitized warning.
- History write errors keep the in-memory summary and allow recovery.
- Message replacement errors fail the run rather than pretending compression occurred.
- An over-limit minimal summary or repeated compression loop routes to Final failure.
- All failure events omit raw provider exceptions, credentials, absolute sensitive paths, and
  complete untrusted payloads.

## Testing Strategy

All default tests are offline and isolate the repository `.env`.

### Memory And Storage Tests

- Independent, bounded Rules/Working/History layers.
- Per-field truncation, source projection, six-handoff and three-event limits.
- Missing history read, replace/read round trip, UTF-8, NUL, size, link, and fixed-path behavior.
- No agent receives a History write tool.

### Monitor Tests

- Model tokenizer count and deterministic fallback count.
- No provider invocation during counting.
- Exact boundary behavior at 399,999 and 400,000.
- Explicit small test threshold without changing the production default.
- Normal and compression routes.

### Compressor Tests

- Valid function-calling structured compression.
- Provider error, invalid schema, and empty-output fallback.
- Removal of all previous messages and addition of one recovery summary.
- History persistence and persistence-warning behavior.
- Before/after metrics, bounded state fields, minimal fallback, and loop-stop behavior.
- No exception or secret detail leakage.

### Workflow Tests

- `Supervisor -> Monitor -> Verifier -> Final` without compression.
- `Supervisor -> Monitor -> Compressor -> Supervisor` with compression.
- Specialist continuation from compressed Memory without repeating completed work.
- Verifier failure and bounded retry after compression.
- Unrecoverable compression ends in failure.
- Stage 2 and Stage 3 graph tests remain green.

### Core And CLI Tests

- Stage 4 is the default builder.
- Context events normalize without duplicating nested agent events.
- Monitor and Compressor panels remain literal, bounded, and GBK compatible.
- Exit codes and existing Stage 3 panels remain unchanged.

### Opt-In Live Acceptance

`--run-live-stage4` uses the configured DeepSeek-compatible model. It builds Stage 4 directly with
a deliberately small test threshold so a finite multi-file task triggers one real compression,
then proves that execution resumes and Verifier passes. It does not start a GUI or background
server and remains skipped by default.

## Package And Documentation

- Package version becomes `0.4.0`.
- Add `docs/stage4.md` as the learning and operating guide.
- Add `docs/stage4-plan.md` only after this design is approved.
- Keep README unchanged.
- Add no new runtime dependency unless implementation proves the existing model tokenizer and
  LangGraph message APIs are insufficient.

## Acceptance Criteria

- Production state defaults to a fixed 400,000-token limit.
- Monitor counts messages plus bounded layered Memory and has a deterministic fallback.
- Context at or above the limit routes through Compressor before Verifier.
- Compression replaces raw messages with one bounded recovery summary.
- `HISTORY_SUMMARY.md` and `NOTEPAD.md` survive transcript deletion.
- Supervisor and specialists receive enough bounded Memory to resume without guessing.
- SearchAgent and CodeAgent retain their Stage 3 tool boundaries.
- Compression failure cannot cause state loss, infinite loops, secret leakage, or false success.
- Verifier remains independent and evidence-driven.
- Stage 2 and Stage 3 remain available and pass their regression tests.
- Stage 4 becomes the default CLI workflow with separate context panels.
- Default tests make no paid model or Tavily calls.
- Full offline tests, Ruff, CLI help, and Git hygiene checks pass.

## Explicitly Deferred

- Human approval and command-risk classification.
- Checkpoint save/resume.
- Multi-turn sessions and intent routing.
- Textual TUI and approval dialogs.
- Parallel or background specialists.
- Dynamic model-specific context limits and user-facing threshold configuration.
