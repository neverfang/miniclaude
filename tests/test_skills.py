import pytest

from miniclaude.skills.catalog import SkillError, discover_skills, load_skill


def test_discovers_bounded_project_skill(tmp_path):
    path = tmp_path / ".miniclaude" / "skills" / "review" / "SKILL.md"
    path.parent.mkdir(parents=True)
    path.write_text("Review changes carefully.", encoding="utf-8")

    skills = discover_skills(tmp_path)

    assert [skill.name for skill in skills] == ["review"]
    assert load_skill(tmp_path, "review").content == "Review changes carefully."


@pytest.mark.parametrize("name", ["../escape", "bad name", "", ".hidden"])
def test_rejects_invalid_skill_name(tmp_path, name):
    with pytest.raises(SkillError):
        load_skill(tmp_path, name)


def test_rejects_oversized_skill(tmp_path):
    path = tmp_path / ".miniclaude" / "skills" / "large" / "SKILL.md"
    path.parent.mkdir(parents=True)
    path.write_text("x" * 20_000, encoding="utf-8")

    with pytest.raises(SkillError, match="large"):
        load_skill(tmp_path, "large")


def test_discovery_ignores_invalid_entries(tmp_path):
    root = tmp_path / ".miniclaude" / "skills"
    (root / "valid").mkdir(parents=True)
    (root / "valid" / "SKILL.md").write_text("ok", encoding="utf-8")
    (root / "bad name").mkdir()
    (root / "bad name" / "SKILL.md").write_text("bad", encoding="utf-8")

    assert [skill.name for skill in discover_skills(tmp_path)] == ["valid"]
