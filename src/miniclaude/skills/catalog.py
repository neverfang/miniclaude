"""Validate and load bounded project-local SKILL.md files."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

MAX_SKILL_BYTES = 16_384
SKILL_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


class SkillError(ValueError):
    """A safe validation error for a project Skill."""


@dataclass(frozen=True)
class Skill:
    name: str
    path: Path
    content: str


def _is_link(path: Path) -> bool:
    return path.is_symlink() or (hasattr(path, "is_junction") and path.is_junction())


def _skill_root(project: Path) -> Path:
    return Path(project).resolve() / ".miniclaude" / "skills"


def load_skill(project: Path, name: str) -> Skill:
    if not isinstance(name, str) or SKILL_NAME.fullmatch(name) is None:
        raise SkillError("Invalid Skill name")
    root = _skill_root(project)
    directory = root / name
    path = directory / "SKILL.md"
    if _is_link(directory) or _is_link(path):
        raise SkillError("Skill links are not allowed")
    try:
        resolved = path.resolve(strict=True)
    except (FileNotFoundError, OSError) as exc:
        raise SkillError(f"Skill does not exist: {name}") from exc
    if not resolved.is_relative_to(root.resolve()) or not resolved.is_file():
        raise SkillError("Skill path is outside the project catalog")
    try:
        size = resolved.stat().st_size
        if size > MAX_SKILL_BYTES:
            raise SkillError(f"Skill is too large: {name}")
        content = resolved.read_text(encoding="utf-8")
    except UnicodeError as exc:
        raise SkillError(f"Skill must be UTF-8 text: {name}") from exc
    except OSError as exc:
        raise SkillError(f"Skill is unreadable: {name}") from exc
    if not content.strip():
        raise SkillError(f"Skill is empty: {name}")
    return Skill(name=name, path=resolved, content=content)


def discover_skills(project: Path) -> tuple[Skill, ...]:
    root = _skill_root(project)
    if not root.is_dir() or _is_link(root):
        return ()
    result = []
    try:
        entries = sorted(root.iterdir(), key=lambda item: item.name)
    except OSError:
        return ()
    for entry in entries:
        try:
            result.append(load_skill(project, entry.name))
        except SkillError:
            continue
    return tuple(result)
