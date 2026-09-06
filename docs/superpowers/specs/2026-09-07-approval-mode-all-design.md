# `approval-mode=all` Design

## Goal

Add an explicit `all` approval mode that asks the user for confirmation before every executable
shell command. The feature must preserve the existing shell opt-in gate and must never make a
blocked command approvable.

## User-facing behavior

The CLI accepts four approval modes:

- `inline`: safe commands run directly; risky commands require interactive approval.
- `all`: safe and risky commands require interactive approval.
- `auto`: safe and risky commands run without interactive approval.
- `deny`: safe commands run directly; risky commands are denied.

Blocked commands are denied in every mode. `--allow-shell` remains the master switch: when it is
absent, BashTool rejects the command before classification or approval.

Example:

```powershell
miniclaude --resume "D:\workspace\miniclaude\.miniclaude\workspaces\b95e49d72e31" --allow-shell --approval-mode all
```

## Runtime design

`RuntimeState.approval_mode` gains `all` as a valid literal and validation choice. The BashTool
continues to classify each command as `safe`, `risky`, or `blocked` before execution.

The approval decision flow is:

1. Reject immediately when `allow_shell` is false.
2. Classify the command.
3. Reject `blocked` commands without invoking the approval handler.
4. In `all` mode, create an approval request for both `safe` and `risky` commands.
5. In the existing modes, retain their current safe/risky behavior.
6. Execute only after the selected policy has approved or bypassed the command.

The existing inline handler is reused by both `inline` and `all`. In a non-interactive terminal,
the handler remains fail-closed and denies the request.

For an `all`-mode request involving a classifier-safe command, the event/result metadata reports
the classifier's original `risk_level` as `safe` and reports `requires_approval=true`. This keeps
the audit trail truthful: the command is safe according to the classifier but requires approval
because of policy.

## CLI and presentation

The CLI validation and help text list `inline`, `all`, `auto`, and `deny`. Selecting either
`inline` or `all` installs `make_inline_approval_handler`, so the existing Rich
`Approval Required` and resolution panels remain the only interactive UI added in this stage.

No graphical or Textual modal is introduced. That remains separate from the stage-five terminal
workflow.

## Safety and failure behavior

- `all` never bypasses `--allow-shell`.
- `all` never overrides a `blocked` classification.
- Missing handlers, invalid handler return values, input errors, and non-interactive terminals
  deny execution.
- Rejected commands do not start a subprocess.
- Approval request and resolution events are emitted using the existing event schema so traces and
  renderers remain compatible.

## Tests

Implementation follows test-driven development. Tests will first demonstrate these missing
behaviors:

- `RuntimeState` accepts `all` and rejects unknown modes with an updated message.
- CLI accepts `--approval-mode all`, installs the interactive handler, and passes the mode to the
  workflow.
- A safe command in `all` mode requests approval before starting a subprocess.
- Approving a safe command starts it; rejecting it does not.
- A risky command in `all` mode still requires approval.
- A blocked command in `all` mode remains unconditionally blocked and never calls the handler.
- Existing `inline`, `auto`, and `deny` behavior remains unchanged.

The focused approval and CLI tests will run first, followed by the complete test suite.

## Documentation

The stage-five user guide and CLI examples will list the new mode, explain that `all` still
requires `--allow-shell`, and distinguish terminal confirmation from a GUI modal.

## Non-goals

- Allowing users to approve commands classified as `blocked`.
- Persisting approval decisions across commands or runs.
- Pattern-based allowlists or per-command trust rules.
- Adding a graphical/TUI approval modal.
- Changing the command risk classifier.
