"""Keep test artifacts in the ignored runtime directory, never alongside source."""

from uuid import uuid4


def pytest_addoption(parser):
    parser.addoption(
        "--run-live", action="store_true", help="Call paid model API and run generated add code"
    )
    parser.addoption(
        "--run-live-snake",
        action="store_true",
        help="Call model and run generated snake logic (no GUI)",
    )
    parser.addoption(
        "--run-live-stage2",
        action="store_true",
        help="Call the stage-two graph and run generated Game of Life code",
    )


def pytest_configure(config):
    if config.option.basetemp is None:
        root = config.rootpath / ".miniclaude/tmp"
        root.mkdir(parents=True, exist_ok=True)
        config.option.basetemp = str(root / uuid4().hex)
