# Miniclaude Project Skill Installer Design

## Goal

Add a deterministic project-level Skill installer so Miniclaude can install single
Skills and multi-Skill repositories such as `obra/superpowers` into the catalog it
actually loads. Installation must require one explicit Y/N decision and must not grant
the model write access outside its Session workspace.

## User interface

The local Slash command registry gains:

- `/skill install <source>`: inspect and install a local directory or supported GitHub
  repository.
- `/skill update <name>`: reinstall one previously installed Skill from its recorded
  source and revision policy.
- `/skill remove <name>`: remove one managed Skill after confirmation.
- `/skill doctor`: validate the catalog and report invalid, oversized, linked, missing,
  or conflicted entries.

`/skills` continues to list installed Skills and the active Skill. Natural-language
requests to install a Skill do not delegate to CodeAgent. They return an actionable
message directing the user to `/skill install` so the permission boundary remains
visible.

## Approval contract

Install, update, and remove run as local TUI workers. Before any network access or
project-level mutation, the worker creates one immutable `ApprovalRequest` whose tool
name is `SkillInstaller`. The modal shows:

- operation;
- normalized source;
- project destination;
- whether network access is required;
- overwrite or removal scope.

Y authorizes exactly that transaction. N, Enter, Escape, cancellation, or closing the
TUI rejects it. Approval does not change Shell policy and cannot authorize another
Skill operation. Repository scripts, hooks, package installers, and executables are
never run.

## Source policy

Version one accepts:

1. a local directory resolved inside the startup project; or
2. GitHub shorthand `owner/repository`; or
3. `https://github.com/owner/repository[.git]`.

Other URL schemes, credentials in URLs, query strings, fragments, arbitrary Git hosts,
relative traversal, links, and junctions are rejected. GitHub sources are fetched with
an argument vector and `shell=False` into a unique staging directory under
`.miniclaude/cache/skill-installs`. The child receives a filtered environment.

## Package discovery

The staged package scanner supports both layouts:

```text
repository/SKILL.md
repository/skills/*/SKILL.md
```

For a collection repository, every direct child containing `SKILL.md` is treated as an
independent Skill. The complete child directory is copied so referenced `scripts/`,
`references/`, and `assets/` remain available. Nested repositories and `.git` metadata
are excluded.

Each Skill must satisfy:

- normalized name matching `[a-z0-9][a-z0-9_-]{0,63}`;
- regular UTF-8 `SKILL.md` no larger than 32 KiB;
- no link or junction anywhere in its copied tree;
- no more than 100 regular files and 5 MiB total content;
- no protected credential files such as `.env`;
- frontmatter name, when present, must match the directory name.

These bounds accept all 14 current `obra/superpowers` Skill instruction files while
keeping context and copy operations bounded.

## Transaction and metadata

Validated Skills are copied into temporary sibling directories and atomically renamed
to `.miniclaude/skills/<name>`. A normal install never overwrites an existing name; it
reports conflicts and may still install non-conflicting Skills from the same package.
Update is allowed only for managed entries and uses a recoverable backup until the new
copy passes catalog validation.

`.miniclaude/skills/index.json` records format version, source, installed Skill names,
commit revision when available, and timestamps. It contains no credentials. Removing a
managed Skill updates the index atomically.

## Runtime loading

The catalog limit increases from 16 KiB to 32 KiB. Active Skill instructions are loaded
in full; Miniclaude must never cut a `SKILL.md` in the middle. Before a model call, the
Session controller calculates the combined bounded Session and Skill context. If the
selected Skill cannot fit the configured context budget, activation fails locally with
an actionable message rather than injecting partial instructions.

Referenced Skill files remain project metadata. Future resource-loading support may
expose explicitly referenced files, but this installer does not grant ordinary Agent
tools access to the project-level Skill directory.

## Workflow reliability fixes

Skill installation verification is deterministic and does not use model-generated
Shell sentences. It checks destination paths through `SkillCatalog`, compares installed
names with the transaction result, and records a structured success or failure event.

The outer workflow keeps execution status and acceptance status separate. A specialist
model-loop limit remains visible as a warning, but cannot overwrite a later deterministic
acceptance result. Todo schema errors include the required `id`, `content`, and `status`
field names and identical invalid calls stop after one retry.

## Events and presentation

The TUI emits dedicated cards for:

- `skill_install_requested`;
- `skill_install_progress`;
- `skill_installed`;
- `skill_conflict`;
- `skill_install_failed`;
- `skill_removed`;
- `skill_doctor`.

Cards show human-readable summaries first. Diagnostic metadata stays bounded and is not
rendered as raw JSON unless the user explicitly opens details.

## Testing

Tests use local fixture packages and an injected fake repository fetcher. Coverage must
prove:

- no files or network work occur before Y;
- one approval cannot authorize a second operation;
- Superpowers-style `skills/*/SKILL.md` installs as separate catalog entries;
- root `SKILL.md` packages install correctly;
- conflicts never overwrite existing Skills;
- traversal, links, protected files, invalid UTF-8, mismatched names, too many files,
  and size violations fail closed;
- interrupted installs leave neither partial destinations nor stale staging data;
- `/skill doctor` reports catalog problems;
- installed Skills appear in `/skills` and can be activated;
- verifier uses deterministic catalog checks rather than generated Shell text;
- the existing Session, approval, Slash, TUI, and workflow test suites remain green.
