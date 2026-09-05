from importlib.metadata import version

from miniclaude import __version__


def test_stage_three_package_version_is_consistent():
    assert __version__ == "0.3.0"
    assert version("miniclaude") == __version__
