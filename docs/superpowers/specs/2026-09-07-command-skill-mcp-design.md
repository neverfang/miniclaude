# Miniclaude Command, Skill, MCP, and Approval UX Design

## Goal

Extend the Stage 6 interactive TUI with local slash commands, portable project skills,
and MCP tool integration while preserving the existing Session, Trace, checkpoint, and
fail-closed approval boundaries.

The first delivery increment covers capability feedback, exact-command approval, and
the slash-command foundation. Skill and MCP support build on the same registries in
later increments.

## Non-goals

- A slash command must never bypass tool policy or silently enable Shell.
- Approving one command must not approve later commands.
- Skills are bounded instruction/context packages, not executable plugins.
- MCP servers must not receive unrestricted access to local secrets or files.
- Miniclaude will not automatically read a user's Codex configuration directory.

## Safety model

Shell execution keeps two independent gates:

1. `--allow-shell` opts the process into local command execution.
2. The approval mode decides whether an eligible command needs a per-call decision.

For the requested human-reviewed mode, the user starts the TUI with:

```powershell
uv run miniclaude -c --allow-shell --approval-mode all
```

Every eligible Shell invocation then creates a unique immutable `ApprovalRequest`.
The modal shows the exact command and accepts `Y` once or `N`/Escape/Enter to deny.
The decision resolves only that request ID. Blocked commands remain unapprovable.

When Shell is disabled, Miniclaude must render a capability-blocked event containing
the exact restart flags instead of implying that an approval modal is pending.

## Architecture

### Command registry

`CommandRegistry` is UI-independent. It parses only the leading command name and
passes the remaining bounded text to the selected handler. It returns a structured
`CommandResult` containing display text and optional local state actions.

The TUI checks commands before starting a model turn. Unknown commands produce a local
error card and are never sent to the model. Full-width `／` may be normalized to `/`;
Windows backslash paths are not treated as commands.

Initial commands:

- `/help` — list commands and usage.
- `/status` — show Session, workspace, route, and runtime policy.
- `/new` — create a new Session when idle.
- `/clear` — clear visible cards without deleting persisted conversation.
- `/workspace` — show the current Session workspace.
- `/plan` — toggle the plan panel.
- `/approvals` — explain `allow_shell`, approval mode, and the exact restart command.
- `/skills` — show the Skill integration status and discovered skills.
- `/mcp` — show the MCP integration status and configured servers.
- `/tools` — show built-in and connected tool names.
- `/exit` — exit when idle, failing pending approvals closed.

Session switching, forced compaction, Skill activation, and MCP connection commands
are added with their corresponding subsystem so the initial commands do not pretend
unsupported behavior exists.

### Skill catalog

Project-local skills live under `.miniclaude/skills/<name>/SKILL.md`. Discovery rejects
links/junctions, invalid names, oversized files, and paths outside the startup project.
A skill is activated explicitly with `/skill <name>` and removed with `/skill off`.
The active skill is injected as bounded, clearly delimited context and is never treated
as permission to execute tools. `/skills` lists validation status without exposing file
contents.

### MCP manager

MCP configuration lives in a versioned `.miniclaude/mcp.json`. The first transport is
local stdio using an argument vector and `shell=False`; streamable HTTP may be added
after the lifecycle and approval contracts are stable.

Discovered tools are namespaced as `mcp__<server>__<tool>`. They are adapted into the
existing tool registry and emit the same tool-call, tool-result, trace, timeout, and
sanitization events as built-in tools. MCP tools default to per-call approval unless
the configuration explicitly marks a tool read-only. Write-capable tools always need
human approval. Server startup never inherits API-key environment variables unless
the user explicitly maps named variables in configuration.

### Capability-aware workflow

Workflow prompts receive a compact capability summary derived from runtime state.
When Shell is disabled, planners must not publish Shell verification commands. Tool
validation errors return required field names and a bounded example, and repeated
identical failures are stopped instead of consuming the whole loop budget.

Agent handoff summaries report successful and failed actions separately. A failed last
tool call cannot erase earlier successful reads or writes. Verification distinguishes
static inspection from commands actually executed; only the latter can claim runtime
verification.

## TUI behavior

- Typing `/` opens a bounded command suggestion list.
- Selecting or submitting a command renders a local system card.
- Local commands do not increment Session turns or call the model.
- During a running turn, read-only commands such as `/status` may run; mutating commands
  such as `/new` and `/exit` fail with an explanatory message.
- Approval modal focus takes priority over the prompt and no queued command can answer
  an approval accidentally.
- A capability-blocked card explains why no modal appeared and shows the safe restart
  command without attempting to restart the process.

## Persistence and observability

Slash-command execution is not conversation content. State-changing commands append a
sanitized local event; approval and MCP tool activity continue through Trace. Session
format changes are versioned and backward compatible. Secrets, full environment values,
and raw invalid MCP payloads are never persisted.

## Verification

Tests cover:

- Shell disabled produces a capability-blocked event and no approval gate.
- `--allow-shell --approval-mode all` asks once for each safe or risky command.
- Y starts only the exact approved command; N and closing the modal start nothing.
- Slash commands never call the model and unknown commands render locally.
- Commands respect active-turn constraints and do not increment Session turns.
- Skill discovery rejects links, traversal, invalid names, and oversized files.
- A fake local MCP server validates discovery, namespacing, approval, timeout,
  cancellation, output bounding, and secret filtering without network access.

