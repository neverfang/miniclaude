# Stage 6 TUI, Session, and Intent Routing Design

## Goal

Stage 6 adds a local Textual interaction layer to Miniclaude. It provides a visual event stream,
persistent multi-turn sessions, model-based chat/workflow routing, and modal shell approval while
preserving the one-shot CLI and the Stage 5 execution harness.

Real Feishu Bot API integration is deferred. This stage keeps the session controller and event
contracts independent of Textual so a remote messaging adapter can be added later.

## Scope

Stage 6 includes:

- a Textual TUI launched when no task is supplied;
- isolated persistent sessions and continued conversations;
- a DeepSeek-backed intent router and a tool-free chat responder;
- an adapter around the existing Stage 5 workflow event stream;
- a live right-side Session status panel;
- a Textual approval modal backed by a thread-safe approval gate;
- offline unit, controller, and TUI tests plus explicit opt-in live acceptance.

Stage 6 does not include Feishu credentials, webhooks, long-polling, public HTTP endpoints, remote
message delivery, or a graphical desktop application.

## CLI compatibility

The existing one-shot interface remains available:

- `miniclaude "task"` runs the existing one-shot CLI workflow.
- `miniclaude --resume <workspace>` uses the existing Stage 5 checkpoint resume path.

The new interactive interface behaves as follows:

- `miniclaude` always creates a new Session and starts the TUI.
- `miniclaude -c` and `miniclaude --continue` restore the most recently updated Session and its
  workspace.
- `miniclaude --session <session-id>` opens a specific Session.
- `--continue` fails with an actionable usage error when no prior Session exists; it does not
  silently create one.
- `--continue`, `--session`, `--resume`, and a positional task are mutually validated so ambiguous
  combinations fail before model creation or filesystem mutation.

The current Stage 5 policy flags remain usable in TUI mode, including `--allow-shell`,
`--approval-mode`, `--checkpoint-mode`, and `--trace-mode`.

## Architecture

```text
Textual TUI
  | user input / cancellation / approval decision
  v
Session Controller
  +-- Session Store
  +-- Intent Router
  +-- Chat Responder
  +-- Workflow Adapter --> existing stream_workflow_events
                              |
                              v
                         Agent Event Bus
                              |
                              +-- plan/todo
                              +-- tool_call/tool_result
                              +-- handoff
                              +-- checkpoint/trace
                              +-- approval
                              +-- final
```

The Session Controller owns one turn at a time. It records the user turn, builds bounded context,
runs routing, selects chat or workflow execution, streams normalized events, and records the
assistant turn. The controller does not render UI widgets.

The Textual app owns presentation and input. Agent and model work runs in a background worker.
Worker events cross into the Textual thread through Textual messages; widgets are never mutated
directly from the worker.

The existing graph, tools, checkpoint manager, trace writer, and one-shot Rich renderer remain
usable without importing Textual.

## Session persistence

Session data lives below the project startup directory:

```text
.miniclaude/
└── sessions/
    ├── index.json
    └── <session-id>/
        ├── session.json
        ├── SESSION_SUMMARY.md
        └── workspace/
```

`index.json` records bounded Session metadata and identifies the most recently updated Session.
Each Session owns a dedicated generated-file workspace. A new plain `miniclaude` launch creates a
new ID and workspace even when prior sessions exist.

`session.json` contains:

- format version;
- Session ID;
- turn index;
- bounded recent user and assistant turns;
- assistant route (`chat` or `workflow`), content, and summary;
- created and updated timestamps;
- relative workspace identity;
- references to the latest checkpoint and trace IDs when available.

Writes use a temporary file and atomic replacement. Session loading strictly validates the format,
ID, workspace boundary, turn ordering, roles, routes, timestamps, and bounded field types. Invalid
files are not overwritten automatically.

Session persistence uses the existing sanitizer contract. API keys, authorization headers,
credential-like fields, and absolute external paths are not persisted. Session summaries contain
no raw Trace event bodies.

## Session context

Each turn receives a context string capped at 7000 characters. It contains:

- Session ID and current turn index;
- up to the 30 most recent safe workspace file paths;
- up to the 10 most recent bounded turn summaries or contents;
- the previous workflow result summary when present.

Protected paths such as `.env`, `.git`, and `.miniclaude` are excluded from the workspace listing.
The context builder never reads file contents. Newer conversation information takes priority when
the total would exceed the bound.

## Intent routing

Every submitted turn first invokes the configured OpenAI-compatible DeepSeek model with an intent
router prompt. The model must return only:

```json
{"route":"chat","reason":"ordinary greeting","confidence":0.96}
```

The accepted routes are `chat` and `workflow`. A route is accepted only when the payload is valid,
the confidence is finite and between 0 and 1, and confidence is at least 0.55. Invalid JSON,
unknown routes, low confidence, provider errors, and parsing errors all fail safely to `workflow`.

The router uses recent Session context to recognize workflow continuations. Short messages such as
"继续", "修一下", or "运行测试" route to workflow when they refer to prior workspace work.

The chat responder receives the latest user text and bounded Session context. It has no tools and
must not claim to have read files, searched the web, executed commands, or changed the workspace.

## Workflow adapter and event model

The workflow adapter calls the existing Stage 5 event stream in the Session workspace. It
normalizes nested ReAct events into a stable UI event envelope without deleting the original event
payload required by existing traces.

The UI distinguishes at least:

- route decisions;
- plans and todo snapshots;
- agent handoffs;
- tool calls;
- tool results;
- context monitor/compression events;
- checkpoint saves;
- trace summaries;
- approval requests and resolutions;
- final answers and failures.

Conversation output contains only user turns, chat responses, and workflow final answers. Tool and
orchestration details appear in the Event Stream instead of being rendered as raw JSON in the
conversation.

## Textual layout

The normal-width layout uses a main execution column and a fixed-width Session sidebar:

```text
+ miniclaude -----------------------------------------------+
| Logo / current task                                       |
+--------------------------------------+--------------------+
| Plan                                 | Session            |
| [done] analyse [run] code [todo] test| status    running  |
+ Event Stream                         | turns     3        |
| HANDOFF planner -> codeAgent         | session   ab12cd   |
| TOOL CALL BashTool                   | route     workflow |
| TOOL RESULT success / exit 0 / 1.2s | workspace .../work|
+ Conversation                         | checkpoint saved   |
| You: add login                       | trace      84fc... |
| Assistant: completed...              | tools      6 / 1  |
|                                      | approvals  2      |
|                                      | tokens     31k/400k|
|                                      | todo       2 / 3  |
+--------------------------------------+--------------------+
| > input                                             [Send] |
+------------------------------------------------------------+
```

The sidebar is approximately 28 to 34 terminal cells wide and shows:

- status: `idle`, `routing`, `chatting`, `planning`, `running`, `verifying`,
  `waiting approval`, `completed`, `failed`, or `cancelled`;
- turn count and Session ID;
- current route;
- shortened workspace path with the full path available on focus;
- latest checkpoint state and timestamp;
- current Trace ID;
- total and failed tool calls;
- approval count and pending state;
- context token usage and limit;
- completed and total todos.

The sidebar is updated from aggregated events, not inferred independently by widgets. Waiting
approval is yellow, failure is red, and completion is green.

At narrow widths, the sidebar collapses into a one-line status summary above the conversation.
`Ctrl+S` toggles it. Plan can collapse before Conversation or the input area loses usable space.

Tool calls, tool results, handoffs, checkpoints, and trace summaries render as separate styled
cards. Long commands and outputs are bounded and scrollable. Raw event JSON is not the primary UI.

## Input and lifecycle

- Enter submits when the input is focused and no approval modal is open.
- A turn in progress disables duplicate submission but keeps scrolling, approval, and cancellation
  responsive.
- The first `Ctrl+C` cancels the active turn and leaves the TUI running; a second `Ctrl+C` exits.
- `Ctrl+L` clears visible event cards without deleting Session, checkpoint, or trace data.
- `Ctrl+N` creates and switches to a new Session after safely cancelling any active turn.
- `Ctrl+O` opens a Session selector.
- Normal exit atomically saves the Session.

Only one turn may execute within a Session at a time.

## Modal approval

The TUI reuses Stage 5 `ApprovalRequest` and `ApprovalDecision` contracts. A background BashTool
approval handler creates an `ApprovalGate`, posts an approval message to Textual, and waits. The UI
opens `ApprovalModal`; resolving or dismissing it releases the worker.

The modal shows the tool name, classifier level, risk reason, bounded workspace path, and the exact
sanitized command.

- `Y` or clicking Approve approves.
- `N`, Escape, Enter, or clicking Deny rejects.
- Closing the app, cancelling the turn, worker failure, or UI failure rejects all pending gates.
- Resolving a gate more than once has no effect.
- Blocked commands never create a gate or modal.

The `all` mode opens the modal for safe and risky commands. The `inline` mode opens it only for
risky commands. `auto` and `deny` retain their Stage 5 behavior.

## Error handling

- Background exceptions produce a sanitized failure event and set Session status to `failed`.
- Intent-routing failures select `workflow` and preserve a bounded diagnostic reason.
- Corrupt Session files fail with an actionable error and remain untouched.
- Duplicate submissions are rejected without creating a partial turn.
- App shutdown resolves all pending approval gates as denied and requests worker cancellation.
- Session save failure is visible and does not produce a false completed state.
- Checkpoint and Trace behavior remains governed by the Stage 5 harness.

## Testing

Offline tests cover:

- Session creation, new-by-default launches, recent and explicit Session selection, atomic writes,
  strict validation, turn bounds, context bounds, workspace listing, and sanitization;
- intent parsing for chat, workflow, low confidence, invalid JSON, non-finite confidence, model
  exceptions, and context-dependent continuation wording;
- chat execution without tools and workflow event forwarding;
- controller turn serialization, persistence, cancellation, and failure states;
- ApprovalGate approve, reject, shutdown, duplicate resolution, and no-deadlock behavior;
- Textual pilot interactions for sidebar updates, separate event cards, input locking, shortcuts,
  responsive sidebar state, and modal keyboard/button behavior;
- regression coverage for one-shot CLI, Resume, `approval-mode=all`, checkpoints, traces, and
  existing workflow stages.

Real DeepSeek acceptance is behind a new explicit opt-in flag. Default tests make no paid model or
Tavily calls and execute no real risky command.

## Completion criteria

Stage 6 is complete when:

1. Plain `miniclaude` always creates a new isolated Session and launches the TUI.
2. `miniclaude -c` and `miniclaude --continue` restore the latest Session and workspace.
3. Chat input takes the tool-free chat path and work input takes the existing workflow path.
4. Follow-ups such as "继续" receive bounded context from prior turns.
5. Plans and each tool/handoff/checkpoint event render separately.
6. The right Session sidebar reflects real aggregated state.
7. `approval-mode=all` produces a safe-by-default TUI modal for safe and risky commands.
8. Cancellation, failure, and shutdown leave no worker permanently waiting for approval.
9. The existing one-shot CLI and Stage 5 Resume path remain compatible.
10. The full offline test suite and Ruff pass.

## Deferred work

- Feishu Bot API and remote transport.
- Multiple concurrently executing turns in one Session.
- Cross-machine Session synchronization.
- Desktop GUI, browser UI, voice, and media rendering.
- Optional character art or mascot themes.
