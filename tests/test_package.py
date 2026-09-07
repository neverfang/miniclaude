import tomllib
from importlib.metadata import version
from pathlib import Path

from miniclaude import __version__


def test_stage_six_package_version_and_dependency_are_consistent():
    project_file = Path(__file__).parents[1] / "pyproject.toml"
    project = tomllib.loads(project_file.read_text(encoding="utf-8"))["project"]

    assert __version__ == "0.6.0"
    assert version("miniclaude") == __version__
    assert project["version"] == __version__
    assert any(
        dependency.startswith("textual>=8.2")
        for dependency in project["dependencies"]
    )
