# Stage One ReAct Implementation Plan

> Execute locally, in the current directory as requested by the user. Use test-driven-development and verification-before-completion. Review the finished feature independently.

**Goal:** Deliver the five-tool ReAct CLI described in stage1-design.md, with offline and opt-in live acceptance tests.

**Architecture:** A small RuntimeState owns workspace limits and read snapshots. Tools are bound with StructuredTool; the loop injects a model, pairs tool messages and emits events; the CLI handles configuration and rendering.

**Tech Stack:** Python 3.11+, langchain-core, langchain-openai, Typer, Rich, python-dotenv, regex, pytest, uv.

## 1. Tools and runtime

- [x] Add failing tests in tests/test_tools.py for read/write/edit, paths, grep and Shell.
- [x] Run `uv run pytest tests/test_tools.py -q` and establish the missing implementation failure.
- [x] Implement core/state.py, core/paths.py, tools/file_tools.py, tools/grep_tool.py, tools/bash_tool.py and tools/registry.py.
- [x] Verify safe overwrite behavior with a real file: write `before`, read it, externally change it to `after`, then assert an edit raises ToolError and leaves `after` intact.
- [x] Verify command execution with `python -c "print(2 + 3)"`; assert stdout contains 5, exit_code is 0, and disabled Shell cannot launch it.
- [x] Rerun tools tests; fix only failures within the design.

## 2. ReAct and provider

- [x] Add tests/test_agent.py and tests/test_provider.py before implementation.
- [x] Verify the new tests fail because the loop/provider do not exist.
- [x] Implement core/agent.py, core/prompts.py and providers/openai_provider.py.
- [x] Assert the next model input contains a ToolMessage whose tool_call_id equals its preceding AIMessage call ID and whose JSON contains actual tool output.
- [x] Cover max_loops, malformed calls, unknown tools, empty answers, model failure and tool error recovery.
- [x] Run the suite and verify a scripted model creates a Python file and executes it through the real tools.

## 3. CLI and acceptance

- [x] Write tests/test_cli.py for help, argument validation, missing config, event rendering and exit codes.
- [x] Implement cli/app.py and __main__.py only after failing tests.
- [x] Add tests/test_live.py with explicit --run-live / --run-live-snake gates and isolated generated workspaces.
- [x] Add docs/stage1.md with installation, configuration, learning order, exact test commands, expected artifacts and safety limits.
- [x] Run `uv run pytest -q`, `uv run ruff check .`, `uv run ruff format --check .`, and `uv run miniclaude --help`.
- [x] Inspect git diff and directory contents; no generated artifacts or credentials may be staged.
- [x] Request independent code review, fix important findings with regression tests, then rerun all checks.

Real API tests must remain skipped without explicit authorization/configuration. README and the original planning document are unchanged. This plan does not authorize pushing implementation commits to GitHub.
