from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from textual.app import App

from miniclaude.cli.tui.approval import (
    ApprovalGate,
    ApprovalGateRegistry,
    ApprovalModal,
)
from miniclaude.core.approval import ApprovalRequest


def request(identifier: str = "approval") -> ApprovalRequest:
    return ApprovalRequest(
        id=identifier,
        command="python --version",
        risk_level="safe",
        risk_reason="approval-mode=all",
        workspace=Path("workspace").resolve(),
    )


def test_gate_resolves_once_and_wait_returns_decision():
    gate = ApprovalGate(request())

    assert gate.resolve(True, "Approved in TUI") is True
    assert gate.resolve(False, "late denial") is False
    assert gate.wait(timeout=0.01).approved is True


def test_gate_timeout_fails_closed():
    gate = ApprovalGate(request())

    decision = gate.wait(timeout=0.01)

    assert decision.approved is False
    assert "timed out" in decision.reason.lower()


def test_registry_denies_every_pending_gate_on_shutdown():
    registry = ApprovalGateRegistry()
    first = registry.create(request("one"))
    second = registry.create(request("two"))

    registry.deny_all("TUI closed")

    assert first.wait(0).approved is False
    assert second.wait(0).approved is False


def test_approval_is_scoped_to_one_request_only():
    registry = ApprovalGateRegistry()
    first = registry.create(request("one"))
    second = registry.create(request("two"))

    first.resolve(True, "Approved once")

    assert first.wait(0).approved is True
    assert second.resolved is False
    second.resolve(False, "Denied separately")
    assert second.wait(0).approved is False


class ModalTestApp(App):
    def __init__(self, gate):
        super().__init__()
        self.gate = gate

    def on_mount(self):
        self.push_screen(ApprovalModal(self.gate))


@pytest.mark.parametrize(
    ("key", "approved"),
    [("y", True), ("enter", False), ("escape", False), ("n", False)],
)
def test_modal_keyboard_decisions_fail_closed(key, approved):
    async def scenario():
        gate = ApprovalGate(request())
        app = ModalTestApp(gate)
        async with app.run_test() as pilot:
            await pilot.press(key)
            await pilot.pause()
            assert gate.wait(0).approved is approved

    asyncio.run(scenario())
