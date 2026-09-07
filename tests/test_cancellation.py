import pytest

from miniclaude.core.cancellation import CancellationToken, TurnCancelled
from miniclaude.core.state import RuntimeState


def test_cancel_is_idempotent_and_invokes_each_callback_once():
    token = CancellationToken()
    calls = []
    unregister = token.register(lambda: calls.append("stopped"))

    assert token.cancel("Escape pressed") is True
    assert token.cancel("Ctrl+C pressed") is False
    unregister()

    assert calls == ["stopped"]
    assert token.cancelled is True
    assert token.reason == "Escape pressed"


def test_late_registration_runs_immediately_and_checkpoint_raises():
    token = CancellationToken()
    calls = []
    token.cancel("user cancelled")

    token.register(lambda: calls.append("late"))

    assert calls == ["late"]
    with pytest.raises(TurnCancelled, match="user cancelled"):
        token.checkpoint()


def test_runtime_has_independent_cancellation_tokens(tmp_path):
    first = RuntimeState(tmp_path / "one")
    second = RuntimeState(tmp_path / "two")

    first.cancellation.cancel("first")

    assert first.cancellation.cancelled is True
    assert second.cancellation.cancelled is False
