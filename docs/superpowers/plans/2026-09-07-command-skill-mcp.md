# Command, Skill, MCP, and Approval UX Implementation Plan

> Implement in small test-driven increments. Do not connect a real MCP server until
> the fake-server contract and approval boundary pass locally.

## Increment 1 — Approval feedback and slash foundation

### Task 1: Surface disabled Shell as a capability event

Files:

- Modify: `src/miniclaude/tools/bash_tool.py`
- Modify: `src/miniclaude/cli/tui/widgets.py`
- Test: `tests/test_tools.py`
- Test: `tests/test_tui_app.py`

Steps:

1. Add failing tests for a disabled-Shell structured event and its TUI card.
2. Emit a bounded `capability_blocked` event before returning the existing Tool error.
3. Render a dedicated card with `--allow-shell --approval-mode all` guidance.
4. Run the focused tests and Ruff.

### Task 2: Guarantee one-shot Y/N approval semantics

Files:

- Modify: `src/miniclaude/cli/tui/approval.py`
- Test: `tests/test_tui_approval.py`
- Test: `tests/test_tools.py`

Steps:

1. Add an integration test that resolves one request with Y and confirms a later
   request still waits for its own decision.
2. Add a rejection test proving N starts no process.
3. Preserve the immutable request ID and fail-closed unmount behavior.
4. Run focused approval tests.

### Task 3: Add a UI-independent command registry

Files:

- Create: `src/miniclaude/commands/__init__.py`
- Create: `src/miniclaude/commands/registry.py`
- Test: `tests/test_commands.py`

Steps:

1. Test parsing, aliases, full-width slash normalization, bounds, and unknown commands.
2. Define immutable command metadata and structured results.
3. Register the initial local commands with no Textual dependency.
4. Test that raw command arguments cannot be interpreted as Shell.

### Task 4: Route TUI input through slash commands

Files:

- Modify: `src/miniclaude/cli/tui/app.py`
- Modify: `src/miniclaude/cli/tui/widgets.py`
- Modify: `src/miniclaude/cli/tui/app.tcss`
- Test: `tests/test_tui_app.py`

Steps:

1. Add failing tests that `/help`, `/status`, `/clear`, `/workspace`, `/plan`,
   `/approvals`, `/skills`, `/mcp`, `/tools`, and `/exit` avoid the model turn stream.
2. Add a command-result card and active-turn mutation guards.
3. Add bounded suggestions when the prompt begins with `/`.
4. Keep Ctrl bindings as aliases of the same command handlers.
5. Run all TUI and command tests.

## Increment 2 — Portable project Skills

### Task 5: Discover and validate skills

Files:

- Create: `src/miniclaude/skills/__init__.py`
- Create: `src/miniclaude/skills/catalog.py`
- Test: `tests/test_skills.py`

Steps:

1. Test valid discovery and every path/size/link boundary.
2. Parse bounded SKILL.md metadata without executing content.
3. Return sanitized validation diagnostics.

### Task 6: Activate skills per Session

Files:

- Modify: `src/miniclaude/core/session.py`
- Modify: `src/miniclaude/core/session_controller.py`
- Modify: `src/miniclaude/commands/registry.py`
- Test: `tests/test_session.py`
- Test: `tests/test_commands.py`

Steps:

1. Version the Session format with backward loading support.
2. Add `/skill <name>` and `/skill off`.
3. Inject the active skill as bounded untrusted context.
4. Confirm activation grants no tool capability.

## Increment 3 — MCP bridge

### Task 7: Validate versioned MCP configuration

Files:

- Create: `src/miniclaude/mcp/__init__.py`
- Create: `src/miniclaude/mcp/config.py`
- Test: `tests/test_mcp_config.py`

### Task 8: Implement managed stdio lifecycle

Files:

- Create: `src/miniclaude/mcp/manager.py`
- Modify: `pyproject.toml`
- Test: `tests/test_mcp_manager.py`

Steps:

1. Verify the current official MCP Python SDK before selecting a bounded dependency.
2. Use a local fake server for initialize, list-tools, call-tool, timeout, and shutdown.
3. Launch with argv and `shell=False`; filter environment and bound all output.

### Task 9: Adapt MCP tools into the harness

Files:

- Modify: `src/miniclaude/tools/registry.py`
- Modify: `src/miniclaude/core/state.py`
- Modify: `src/miniclaude/commands/registry.py`
- Test: `tests/test_mcp_tools.py`

Steps:

1. Namespace discovered tools and reject collisions.
2. Apply per-call approval before external calls.
3. Emit normalized events and trace records.
4. Add `/mcp`, `/mcp connect`, `/mcp disconnect`, and `/tools` live status.

## Increment 4 — Workflow reliability hardening

### Task 10: Make planning capability-aware

Files:

- Modify: `src/miniclaude/core/prompts.py`
- Modify: `src/miniclaude/graph/supervisor.py`
- Modify: `src/miniclaude/agents/code_agent.py`
- Test: `tests/test_supervisor.py`
- Test: `tests/test_specialist_agents.py`

### Task 11: Separate static and runtime verification

Files:

- Modify: `src/miniclaude/graph/nodes.py`
- Modify: `src/miniclaude/core/trace.py`
- Test: `tests/test_graph_nodes.py`
- Test: `tests/test_trace.py`

Steps:

1. Stop repeated identical schema failures.
2. Preserve successful operations in handoff summaries.
3. Record whether verification commands actually ran.
4. Reject runtime-success claims when no runtime verification occurred.

## Final verification

Run:

```powershell
uv run pytest -q
uv run ruff check .
```

Then manually verify in a fresh TUI:

```powershell
uv run miniclaude --allow-shell --approval-mode all
```

Submit a task that calls Shell twice. Confirm each command creates a distinct modal,
Y approves only the current command, N denies only the current command, and `/help`
does not create a model turn.

