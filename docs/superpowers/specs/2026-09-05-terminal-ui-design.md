# Miniclaude Terminal UI Design

**Date:** 2026-09-05

## Goal

Replace the current line-oriented JSON output with a readable, sectioned terminal UI inspired by the supplied reference. Tool calls, tool results, workflow stages, and the final outcome must be visually distinct without changing Agent behavior or the event protocol.

## Scope

This change affects only human-facing terminal rendering. It does not change model prompts, tools, graph routing, event payloads, workspace behavior, provider configuration, or README content.

## Architecture

Create `src/miniclaude/cli/render.py` as the presentation boundary. The existing CLI command remains responsible for argument parsing, model setup, workflow execution, and exit codes. It passes each normalized event to a renderer, which converts the event into Rich renderables.

Keeping rendering outside `cli/app.py` prevents the command entry point from accumulating formatting rules and allows the presentation layer to be tested independently.

## Visual hierarchy

Each major event is rendered separately:

- **Workspace:** a compact startup line containing the resolved workspace path.
- **Planner:** a blue panel containing the attempt number, plan summary, Todo list, acceptance criteria, and verification commands.
- **Tool Call - `<tool name>`:** a magenta panel with one argument per section. File content and replacement text use code-style blocks instead of escaped JSON strings.
- **Tool Result - `<tool name>`:** a green panel when `ok=true` and a red panel when `ok=false`. Metadata is separated from `stdout`, `stderr`, file content, matches, and error detail.
- **Actor Summary:** a cyan panel containing attempt status, implementation summary, and current Todo state.
- **Verifier:** a green panel on success and yellow/red panel on failure, with checks and verification-command results shown as individual rows or sections.
- **Final:** a prominent green success panel or red failure panel.
- **Model progress and errors:** compact status lines or dedicated error panels, kept distinct from tool panels.

The renderer retains recognizable labels such as `planner`, `actor`, and `verifier` so copied terminal logs remain searchable.

## Formatting rules

Raw event dictionaries must not be printed as a single JSON line. Structured values are formatted recursively into readable key/value rows.

Short strings are shown in full. Long strings are truncated only in the terminal display and include an explicit notice with the original character count. Truncation does not modify the event passed through the workflow or the content seen by the model.

Multiline values such as source code, `stdout`, and `stderr` preserve line breaks. Empty fields are omitted unless their absence is meaningful. Unknown event types fall back to a compact safe representation instead of crashing the CLI.

Custom status markers and title separators use ASCII (`[ok]`, `[x]`, `[~]`, `-`) so the renderer remains usable on Windows terminals configured with GBK. Rich may still provide terminal-safe panel borders and colors.

Rich markup must not interpret model or tool text. User- and model-controlled content is rendered as literal text to prevent accidental formatting or malformed output.

## Error handling and safety

Existing exit-code behavior remains unchanged. Provider exceptions continue to expose only sanitized exception types and guidance. Tool failures are rendered distinctly but must not reveal secrets beyond the already bounded event payload.

The shell warning remains visible before workflow execution. The UI must continue to work when color is unavailable or output is captured by tests.

## Testing

Use test-driven development:

1. Add renderer tests proving Tool Call and Tool Result appear in separate titled panels.
2. Add tests for multiline code and command output readability.
3. Add a truncation test that checks the notice and original character count.
4. Add Planner, Actor, Verifier, Final, failure, and unknown-event tests.
5. Update CLI integration assertions to verify the new separated sections while preserving exit codes and sanitized failures.
6. Run the complete locked test suite, Ruff lint, Ruff format check, and `git diff --check`.

## Acceptance criteria

- Tool calls and tool results are never combined into one undifferentiated output block.
- A user can visually distinguish Planner, Actor, Verifier, and Final at a glance.
- Large code or command output cannot flood the terminal without a truncation notice.
- Source code and command output remain multiline and readable.
- Agent behavior and workflow event data are unchanged.
- Existing error sanitization, workspace selection, and exit codes remain covered by tests.
- README remains untouched and all changes remain uncommitted until explicitly requested.
