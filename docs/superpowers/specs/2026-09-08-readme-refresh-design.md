# README Refresh Design

## Goal

Turn `README.md` into the complete GitHub landing page for Miniclaude v0.6.0. A new
visitor should understand what the project teaches, configure an OpenAI-compatible
DeepSeek endpoint, start the correct runtime mode, and distinguish implemented features
from planned integrations without reading source code first.

## Audience and language

The primary audience is Chinese-speaking developers learning how coding agents evolve
from a ReAct loop into a persistent terminal application. The document will use concise
Chinese prose while preserving established English technical terms and command names.

## Structure

The README will contain, in order:

1. Project positioning and current release status.
2. Implemented capabilities and the six-stage learning roadmap.
3. A compact architecture and runtime-flow overview.
4. Prerequisites, installation, and `.env` configuration for DeepSeek and Tavily.
5. Commands for new Sessions, continued Sessions, one-shot tasks, and checkpoints.
6. TUI shortcuts, slash commands, and Shell approval modes.
7. Workspace/session data layout, testing, documentation links, and security limits.
8. A short boundary section that marks MCP connection as not yet implemented.

## Accuracy rules

- Treat source code, `pyproject.toml`, `.env.example`, CLI help, and passing tests as the
  authority for current behavior.
- Do not describe design-only material in `项目篇规划.md` as implemented.
- State that project Skills can be discovered and activated, but installation and MCP
  connection are not complete unless the current code proves otherwise.
- Explain that Shell execution is unsandboxed and opt-in.
- Never include real API keys, local Session IDs, generated workspace paths, or logs.

## Presentation

Use ordinary Markdown that renders cleanly on GitHub. Prefer short paragraphs, tables,
and copyable fenced command blocks. Avoid decorative badges, screenshots, large banners,
and claims about package publication or platform support that have not been verified.

## Verification

- Check every documented CLI flag against `python -m miniclaude --help`.
- Check every slash command against the command registry.
- Check configuration names against `.env.example` and provider code.
- Run Markdown-oriented content checks for stale names, placeholders, and leaked secrets.
- Run the default test suite only if README editing changes executable examples or code;
  otherwise preserve the most recent verified result of 415 passed and 8 skipped.
