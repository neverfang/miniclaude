from importlib.metadata import version

from miniclaude import __version__


def test_stage_four_package_version_is_consistent():
    assert __version__ == "0.4.0"
    assert version("miniclaude") == __version__
