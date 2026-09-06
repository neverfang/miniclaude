# Stage Five Harness Engineering Design

**Date:** 2026-09-06

## Goal

Add production-oriented safety and observability around the Stage 4 workflow through human
approval, resumable checkpoints, and durable execution traces. Stage 5 becomes the default CLI
workflow while preserving the earlier stage builders as learning and regression artifacts.

The harness must fail closed around command execution, must not overwrite current workspace files
during ordinary resume, and must never persist plaintext credentials in checkpoint or trace data.

## Scope

Stage 5 adds:

- A three-level shell risk classifier: `safe`, `risky`, and `blocked`.
- `inline`, `auto`, and `deny` approval modes for risky commands.
- Light, strict, and disabled checkpoint modes.
- Semantic resume from the most recent safe graph boundary.
- Optional, explicit workspace restoration from an isolated Git snapshot.
- Append-only, sanitized execution traces and human-readable timelines.
- A small Harness coordinator around the existing Stage 4 event stream.
- Rich panels for approval, checkpoint, resume, trace, and harness warnings.
- Offline tests and a separately authorized live DeepSeek acceptance test.

Stage 5 does not add a Textual TUI, multi-turn sessions, an intent router, background agents, or a
remote messaging integration. Those remain Stage 6 concerns. README remains unchanged until the
complete project is finished.

## Architecture

```text
CLI
  |
  v
RuntimeState
  |-- approval policy and handler
  |-- checkpoint policy
  `-- trace policy
  |
  v
HarnessRunner
  |-- TraceRecorder.start()
  |-- CheckpointManager.save(started)
  |
  `-- Stage 4 Workflow
       |-- Supervisor / Specialists / Verifier
       |-- BashTool -> RiskClassifier
       |                 |-- safe -----> execute
       |                 |-- risky ----> inline / auto / deny
       |                 `-- blocked --> reject
       |-- custom event ------> TraceRecorder
       |-- graph update ------> TraceRecorder + CheckpointManager
       `-- final/error/interrupt -> final checkpoint + trace
```

The harness is an application-layer wrapper, not a new LangGraph reasoning node. It observes
normalized events and graph updates without changing Supervisor, specialist, Context Monitor,
Compressor, or Verifier responsibilities.

Approval remains inside `BashTool` because authorization must be resolved immediately before a
process starts. Checkpoint and Trace remain outside tools so they can observe the entire workflow.

## Files And Responsibilities

- `src/miniclaude/core/approval.py`
  - Classify commands and define approval request, decision, and result contracts.
  - Split compound shell commands conservatively before classification.
- `src/miniclaude/tools/bash_tool.py`
  - Apply the hard block and approval gate immediately before `subprocess.Popen`.
  - Return approval metadata without bypassing the existing `--allow-shell` gate.
- `src/miniclaude/core/state.py`
  - Carry normalized approval, checkpoint, trace, and event callback settings.
- `src/miniclaude/core/checkpoint.py`
  - Atomically persist checkpoint metadata and strict-mode state/events.
  - Maintain an isolated Git snapshot repository and implement validated resume.
- `src/miniclaude/core/trace.py`
  - Sanitize and append events, update counters, and produce final summaries.
- `src/miniclaude/core/harness.py`
  - Coordinate Checkpoint and Trace lifecycle around Stage 4 streaming.
  - Convert non-fatal storage problems into visible warning events.
- `src/miniclaude/core/agent.py`
  - Build RuntimeState, delegate lifecycle work to Harness, and preserve stable application events.
- `src/miniclaude/cli/app.py`
  - Expose approval, checkpoint, trace, resume, and restore options.
  - Implement the synchronous Rich terminal approval handler.
- `src/miniclaude/cli/render.py`
  - Render distinct approval, checkpoint, resume, trace, and warning panels.
- `tests/test_approval.py`, `tests/test_checkpoint.py`, `tests/test_trace.py`,
  `tests/test_harness.py`
  - Test Stage 5 units and integration without network or paid model calls.
- Existing BashTool, core, CLI, package, and live-test files
  - Receive focused regression and acceptance coverage.

## Runtime Contracts

`RuntimeState` gains process-local settings. Callback objects never enter serialized graph state.

```python
approval_mode: Literal["inline", "auto", "deny"] = "inline"
approval_handler: Callable[[ApprovalRequest], ApprovalDecision] | None = None
checkpoint_mode: Literal["light", "strict", "off"] = "light"
trace_mode: Literal["on", "off"] = "on"
trace_id: str | None = None
event_handler: Callable[[dict[str, object]], None] | None = None
```

Normalization functions reject ambiguity at the core boundary. CLI validation reports invalid
values before constructing a workspace. A library caller that requests `inline` without a usable
handler receives a denied risky command rather than implicit approval.

## Command Risk And Human Approval

### Risk Levels

The classifier returns a structured result containing level, reason, and the matching command
segment.

- `safe`: no recognized high-risk pattern; the command may execute when Shell is enabled.
- `risky`: execution requires the configured approval policy.
- `blocked`: execution is rejected in every mode, including `auto`.

The first Stage 5 risky set includes:

- Python dependency changes: `pip install`, `python -m pip install`, `uv add`, `uv sync`, and
  `uv pip install`.
- JavaScript dependency changes: `npm install`, `pnpm install`, `yarn install`, and `yarn add`.
- Network downloads: `curl` and `wget`.
- Long-running development servers: `uvicorn` and `python -m http.server`.

The blocked set covers clearly destructive or system-level operations, including broad recursive
deletion, filesystem formatting, shutdown/reboot, destructive Git reset/clean, and commands that
target a path outside the workspace. The existing workspace boundary and protected-path rules
remain in force. Stage 5 does not claim that shell execution is an operating-system sandbox.

Before matching, the classifier normalizes case and conservatively examines segments following
PowerShell/sh separators such as newlines, `;`, `&&`, `||`, and pipes. Quoted text is handled by a
small bounded scanner rather than by executing or fully interpreting the command. Ambiguous
parsing that contains a high-risk token is classified at the more restrictive level.

### Approval Flow

Each risky command creates an immutable request:

```python
@dataclass(frozen=True)
class ApprovalRequest:
    id: str
    command: str
    risk_level: str
    risk_reason: str
    workspace: Path
    tool_name: str = "BashTool"


@dataclass(frozen=True)
class ApprovalDecision:
    approved: bool
    reason: str = ""
```

The modes behave as follows:

- `inline`: call the injected handler and synchronously wait for a decision.
- `auto`: approve `risky` commands without prompting and retain audit metadata.
- `deny`: reject every `risky` command.

`blocked` never calls the approval handler. Handler absence, exceptions, invalid return values,
non-interactive input, and EOF all deny the command. Approval is per exact command and is not
cached across calls.

The runtime emits `approval_requested` and `approval_resolved` around inline approval. BashTool's
result includes `requires_approval`, `approval_id`, `risk_level`, `risk_reason`, and `approved`.
The result never includes credentials extracted from a command.

## Checkpoint Storage

Each workspace owns one current checkpoint directory:

```text
.miniclaude/checkpoints/
|-- checkpoint.json
|-- RECOVERY.md
|-- snapshot.git/
|-- state.json          # strict only
`-- events.jsonl        # strict only
```

All JSON and Markdown metadata is written to a validated temporary file in the same directory and
replaced atomically. A format version allows future migrations. Persisted paths are
workspace-relative. Runtime callbacks, model objects, provider configuration, API credentials,
environment values, raw exceptions, and non-serializable objects are excluded.

### Modes

- `light` stores resumable semantic state in `checkpoint.json`, the recovery guide, a bounded file
  manifest, and an isolated Git snapshot commit.
- `strict` additionally stores a sanitized serialization of the complete supported graph state and
  an append-only Harness event stream.
- `off` performs no checkpoint filesystem or Git operations.

The light semantic state includes the task, plan, todos, acceptance criteria, verification
commands, attempts, bounded messages and summaries, research/source state, handoffs, Context
Engineering fields, last error, latest node, status, trace linkage, and snapshot commit.

### Isolated Git Snapshot

`snapshot.git` is a separate Git directory whose work tree is the generated workspace. It does not
modify or commit to the user's project repository. Snapshot commands use argument arrays rather
than a shell and configure an internal, non-user identity.

Snapshots exclude `.git`, `.miniclaude`, `.env*`, virtual environments, caches, dependency trees,
secret key files, symlinks/junctions, and files outside configured count/size limits. Oversized or
unsupported files are reported in the manifest. A snapshot failure produces a warning and does not
pretend the checkpoint is restorable.

### Save Boundaries

Checkpoint saves occur at:

- run start;
- completion of each graph node update;
- risky approval request and resolution;
- tool or node failure;
- context compression;
- keyboard interruption;
- deterministic final success or failure.

Repeated events at the same semantic boundary may update metadata without creating redundant Git
commits when the manifest has not changed.

## Resume And Workspace Restoration

Resume is semantic, not instruction-pointer continuation. It restores the most recent complete
state and starts a new Supervisor round. It does not reattach to a half-finished provider request,
subprocess, tool call, or model stream. Any in-flight operation is recorded as interrupted and may
be reconsidered by the Supervisor.

```text
miniclaude --resume <workspace>
miniclaude --resume <workspace> --restore-workspace
```

The normal resume path validates the format, workspace identity, state schema, and snapshot
metadata, then continues using the files currently on disk. It reports manifest drift but does not
overwrite those files.

`--restore-workspace` is the only path that changes files. Before restoration it creates a backup
snapshot of the current eligible workspace. Restoration is limited to tracked snapshot files and
validated workspace-relative paths. It never restores protected files and never deletes
untracked/protected content merely because it is absent from the old snapshot. A restore failure
stops resume and reports the backup snapshot identifier.

The user may provide a new task with resume only if it exactly matches the stored task after
whitespace normalization; otherwise CLI rejects the ambiguity. Omitting the task uses the stored
task.

## Trace Recording

Every run receives an immutable trace directory:

```text
.miniclaude/traces/<trace_id>/
|-- trace.json
|-- events.jsonl
`-- timeline.md
```

A resumed run creates a new trace and links `resumed_from_trace_id` and the checkpoint identifier.
Historical traces are never rewritten.

Each JSONL record contains a monotonically increasing sequence number, timezone-aware timestamp,
elapsed milliseconds, event type, node/role when known, status, and bounded sanitized data. The
recorder understands run lifecycle, graph updates, handoffs, tool calls/results, approvals,
checkpoints, context events, verifier results, warnings, and final output.

`trace.json` contains:

- trace ID, task summary, status, timestamps, and duration;
- latest node and final pass/fail status;
- node visit counts;
- tool call and failed-tool counts;
- approval, checkpoint, handoff, and compression counts;
- a bounded timeline head/tail and omitted-event count;
- resume linkage and non-fatal recorder warnings.

`timeline.md` is a human-readable summary and never copies full model messages, source excerpts,
commands containing suspected credentials, or large tool output.

## Redaction And Bounds

All checkpoint and trace payloads pass through one shared recursive sanitizer before persistence.
It:

- replaces values for names matching key, token, secret, password, authorization, credential, and
  cookie patterns;
- redacts Bearer values, common API-key assignments, URL credentials, and suspicious command-line
  secret options inside strings;
- rejects `.env` content and protected file content;
- limits nesting depth, collection sizes, individual strings, and total serialized event size;
- converts safe scalar and path values deterministically;
- replaces unknown objects with a type-only marker rather than `repr`, which might leak data.

The sanitizer is intentionally conservative. Redacted traces remain useful for event order,
status, tool names, relative paths, exit codes, and bounded error categories.

## Harness Lifecycle And Event Flow

`HarnessRunner` owns one CheckpointManager and TraceRecorder. It accepts normalized Stage 4 custom
events and graph updates, records them once, saves only at defined boundaries, and emits stable
application events for the CLI.

Lifecycle order is deterministic:

1. validate new-run or resume inputs;
2. construct RuntimeState and Harness components;
3. create the initial or resumed graph state;
4. start Trace;
5. save the initial Checkpoint;
6. stream Stage 4 and process each event/update;
7. save the final Checkpoint;
8. close Trace and emit its summary.

On `KeyboardInterrupt`, steps 7 and 8 run with `interrupted` status before the exception reaches the
CLI. On other unhandled exceptions they run with `failed` status, then the sanitized failure is
reported. Checkpoint or Trace persistence errors are non-fatal warning events unless resume or an
explicit restore cannot be validated safely.

## CLI And Rich Rendering

The one-shot CLI gains:

- `--approval-mode inline|auto|deny`, default `inline`;
- `--checkpoint-mode light|strict|off`, default `light`;
- `--trace-mode on|off`, default `on`;
- `--resume PATH`;
- `--restore-workspace`, valid only with `--resume`.

`--allow-shell` remains mandatory before any BashTool execution. Inline approval uses literal Rich
rendering and displays tool name, risk reason, workspace, and the bounded command. `Y` approves and
`N` denies. EOF or a non-interactive terminal denies.

New display event types are:

- `approval_requested` and `approval_resolved`;
- `checkpoint_saved` and `checkpoint_warning`;
- `resume_loaded` and `resume_warning`;
- `trace_started`, `trace_summary`, and `trace_warning`.

Each category has a separate panel. Untrusted strings are rendered as literal text, bounded for
GBK-compatible terminals, and never interpreted as Rich markup.

## Error Handling

- Shell disabled: reject before risk or approval processing.
- Blocked command: reject in every approval mode.
- Approval handler failure or absence: deny the risky command.
- Checkpoint write failure: emit a warning and keep running when this is a new run.
- Trace write failure: emit one bounded warning and keep running without recursive logging.
- Corrupt or incompatible checkpoint: refuse resume before changing workspace state.
- Snapshot failure: mark the checkpoint non-restorable; semantic resume may still proceed.
- Restore failure: stop resume and retain the pre-restore backup snapshot.
- Interrupted subprocess: retain existing process-tree termination behavior, then checkpoint.
- Keyboard interrupt: finalize state best-effort and return exit code 130.
- Provider, tool, or graph error: preserve Stage 4 bounded retry and final-failure semantics.

No failure event includes raw provider exceptions, API credentials, environment values, absolute
sensitive paths, or unbounded untrusted payloads.

## Testing Strategy

Default tests are offline, use temporary workspaces, do not read the repository `.env`, and do not
call DeepSeek or Tavily.

### Approval Tests

- Safe, risky, and blocked commands across Windows and POSIX spelling/case.
- Compound commands, newlines, quoting, and ambiguous high-risk tokens.
- `inline`, `auto`, and `deny` behavior.
- Handler approve, reject, raise, invalid decision, EOF, and missing-handler behavior.
- Blocked commands cannot be approved even in auto mode.
- Shell-disabled behavior remains unchanged.
- Approval metadata and events contain no secrets.

### Checkpoint Tests

- Independent light, strict, and off layouts.
- Atomic replacement and recovery after a simulated interrupted write.
- Bounded, versioned, serializable state and manifest.
- Snapshot isolation from an existing user Git repository.
- Protected files, links, caches, large files, and `.miniclaude` exclusion.
- No-change snapshot deduplication and snapshot failure warnings.
- Semantic resume without workspace overwrite.
- Explicit restore, pre-restore backup, path validation, and failure rollback evidence.
- Corrupt, incompatible, foreign-workspace, and mismatched-task rejection.

### Trace Tests

- Stable sequence, timestamps, elapsed time, and append-only JSONL.
- Lifecycle, graph, tool, approval, checkpoint, handoff, context, verifier, and final events.
- Counters and final statuses for success, failure, and interruption.
- Recursive redaction, suspicious inline strings, truncation, and unknown objects.
- Timeline head/tail limits and Markdown literal safety.
- Resume linkage and immutable historical traces.
- Non-fatal filesystem failure behavior.

### Harness And CLI Tests

- Stage 4 event order remains intact behind Harness.
- Save boundaries do not duplicate graph work or tool execution.
- Successful, failed, and interrupted lifecycle finalization.
- CLI defaults and invalid option combinations.
- Interactive approval through injected input; no real installs or downloads.
- Resume path and explicit restore flag.
- Separate literal Rich panels and GBK-compatible rendering.
- Exit codes: success `0`, verified failure `1`, configuration/resume error `2`, interrupt `130`.

### Opt-In Live Acceptance

An explicit `--run-live-stage5` test may use the configured DeepSeek-compatible model. It runs only
after separate user authorization and uses a temporary workspace. It must exercise one auto-approved
harmless risky command substitute or controlled dependency inspection, produce checkpoints and a
trace, interrupt at a safe boundary, resume, and reach independent verification. It must not install
system packages, launch a GUI, or leave a background service.

## Package And Documentation

- Package version becomes `0.5.0`.
- Add `docs/stage5.md` as the learning and operating guide during implementation.
- Add `docs/stage5-plan.md` only after this design is reviewed.
- Keep README unchanged.
- Add no new runtime dependency unless the existing standard library, Rich, Typer, and Git CLI are
  insufficient.

## Acceptance Criteria

- `--allow-shell` remains the outer authorization gate.
- Risky commands obey inline, auto, and deny modes.
- Blocked commands cannot execute in any mode.
- Approval errors fail closed and every decision is traceable without leaking secrets.
- Light checkpoints persist bounded resumable state, a recovery guide, manifest, and isolated Git
  snapshot.
- Strict checkpoints additionally persist sanitized full state and events; off writes nothing.
- Normal resume never overwrites current workspace files.
- Workspace restoration requires `--restore-workspace` and creates a recoverable pre-restore backup.
- Resume continues from a safe Supervisor boundary without replaying an in-flight process.
- Traces are ordered, append-only, bounded, sanitized, and summarized.
- Trace and new-run checkpoint write failures remain visible but do not falsely fail completed work.
- Corrupt resume or unsafe restore fails before mutation.
- Stage 4 context monitoring, compression, memory, specialists, and independent verification remain
  intact.
- Earlier stage regression tests remain green.
- Default tests perform no network, paid model, package installation, or real risky commands.
- Full pytest, Ruff, CLI help, resume smoke, and Git hygiene checks pass.

## Explicitly Deferred

- Textual TUI and modal approval dialogs.
- Multi-turn sessions and intent routing.
- Remote approval through Feishu or another service.
- Background or parallel agent execution.
- Process reattachment after interruption.
- Operating-system container, VM, or mandatory-access-control sandboxing.
- Organization-wide approval policies and persistent allowlists.
