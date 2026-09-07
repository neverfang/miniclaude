# Miniclaude Runtime Permissions and Cancellation Design

## Goal

Make Shell permissions adjustable from the running TUI and make Ctrl+C or Escape
reliably stop the entire active turn. A cancelled turn must release the input, terminate
its child processes, reject pending approvals, and never return to `running` because of
late events.

## Runtime permission commands

`/approve` becomes the primary command for inspecting and changing the current Shell
policy. `/approvals` remains an alias. The command supports:

- `/approve`: show the current policy and the four available modes;
- `/approve all`: enable Shell and ask Y/N before every Shell command;
- `/approve inline`: enable Shell, run classified-safe commands directly, and ask Y/N
  before classified-risky commands;
- `/approve auto`: enable Shell and automatically approve safe and risky commands;
- `/approve deny`: disable Shell and reject every Shell command.

The existing blocked-command classification is absolute. `/approve auto` cannot run a
blocked command. Each Y decision authorizes only the command displayed in that approval
modal and is not reusable.

Policy changes affect only the current Miniclaude process. They update the mutable TUI
runtime options used to construct the next `RuntimeState`; they do not rewrite project
configuration, environment files, or command-line arguments. A new process starts from
its own CLI options again.

The command registry parses and validates the requested mode, but does not own mutable
policy. It returns a typed action such as `approval-mode:all`; the TUI applies that
action, refreshes the Session sidebar, and renders a readable confirmation card. Invalid
arguments fail locally and list the accepted modes.

Permission changes are unavailable while a turn or approval is active. The user must
cancel or finish that turn first, which prevents a policy change from retroactively
authorizing an already-created request. Slash commands remain local UI commands and are
never exposed as model-callable tools.

## Policy presentation

The Session sidebar shows two independent fields:

- `shell`: `enabled` or `disabled`;
- `approval`: `all`, `inline`, `auto`, or `deny`.

`/status` and `/approve` report the same values. Help text describes actual behavior
rather than recommending a process restart. The CLI flags remain supported and provide
the initial values used by the TUI.

## Cancellation architecture

Each submitted turn receives a unique run identifier and a thread-safe cancellation
token. The token is passed through the TUI worker, Session controller, workflow runtime,
model loop, and tools. Components check it before starting expensive work and at bounded
intervals while waiting.

The TUI owns the active run record: run identifier, cancellation token, and Textual
worker. Events and completion messages carry that run identifier. The UI accepts them
only when they belong to the current active run. This generation check prevents output
from an old worker from changing a newer Session or moving a cancelled Session back to
`running`.

## Ctrl+C and Escape behavior

While a turn is active, Ctrl+C and Escape perform the same cancellation sequence:

1. atomically mark the active token cancelled;
2. change the visible status to `cancelling`;
3. deny every pending approval gate and close its modal;
4. request cancellation of the Textual worker;
5. stop any registered Shell child process tree;
6. persist a cancellation event to the Session and Trace;
7. finalize the matching run as `idle`, re-enable the prompt, and focus it.

Repeated Ctrl+C or Escape during `cancelling` is idempotent. It must not append duplicate
Session history or raise an exception. When idle, Escape is a no-op and Ctrl+C exits the
application, preserving the current behavior for an intentional quit.

Cancellation is cooperative for model/network calls: it cannot forcibly interrupt a
third-party client call that offers no cancellation primitive, but the UI becomes idle
without waiting for that call and discards every late event by run identifier. New work
may begin immediately. The detached call cannot invoke a tool after cancellation because
the runtime token is checked before tool dispatch.

## Shell process termination

`BashTool` replaces its single blocking `process.wait(timeout=...)` with a short polling
loop. On each interval it checks process completion, timeout, and the runtime cancellation
token. Cancellation uses the existing `_stop_tree` behavior: `taskkill /T /F` on Windows
and process-group termination on POSIX, followed by a direct kill fallback.

The tool returns a structured cancelled result containing `ok: false`, `cancelled: true`,
and bounded captured output. A cancelled command is not reported as a timeout or an
approval denial. The process registry is cleared in `finally` so later cancellation
cannot target a reused process identifier.

## Session and Trace semantics

Cancellation adds an explicit terminal event for the turn with the run identifier and a
short user-originated reason. The cancelled user request remains in Session history, but
no fabricated assistant answer is stored. Any partial tool output already emitted stays
visible and traceable.

`cancelling` is a transient UI state. After local cleanup, the Session returns to `idle`
and its turn index remains usable for the next prompt. Resume treats the interrupted turn
as cancelled rather than incomplete, so it does not replay the cancelled operation.

## Error handling

- An invalid `/approve` mode does not change the current policy.
- Failure to terminate a child process is reported as a bounded cancellation diagnostic,
  while the UI still leaves `running` and rejects late events.
- Exceptions raised during cancellation cleanup are isolated per cleanup step so approval
  rejection, worker cancellation, persistence, and prompt recovery still run.
- A completion message received twice or for a stale run is ignored.
- Closing the TUI invokes the same cancellation coordinator before final shutdown.

## Testing

Unit and TUI tests must prove:

- `/approve` reports policy and each supported argument returns the correct typed action;
- `/approvals` remains a functional alias;
- changing modes updates subsequent `RuntimeState` objects without persisting project
  configuration;
- `/approve` is rejected while a turn is active and invalid input leaves policy unchanged;
- `all`, `inline`, `auto`, and `deny` preserve their current Shell safety semantics;
- blocked commands remain blocked in `auto`;
- Ctrl+C and Escape cancel an active turn, deny a pending approval, re-enable the prompt,
  and finish at `idle`;
- idle Escape is a no-op and idle Ctrl+C exits;
- repeated cancellation is idempotent;
- a long-running Shell child and its descendants are terminated;
- a cancelled tool result is distinct from timeout and denial;
- stale events and stale completion messages cannot change current view state;
- cancellation is recorded in Session and Trace, and resume does not replay the turn;
- the existing Slash command, approval, Session, workflow, tool, and TUI suites remain
  green.

## Scope boundary

This increment does not add persistent trust rules, per-command allowlists, operating
system sandboxing, or forced interruption inside third-party model SDK calls. It provides
process-local policy switching, one-request approvals, cooperative runtime cancellation,
and deterministic protection from late events.
