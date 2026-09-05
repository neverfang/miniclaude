from importlib.metadata import version

from miniclaude import __version__


def test_stage_two_package_version_is_consistent():
    assert __version__ == "0.2.0"
    assert version("miniclaude") == __version__
