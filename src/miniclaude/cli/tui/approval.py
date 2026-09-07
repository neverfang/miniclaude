"""Thread-safe bridge between workflow shell approval and Textual."""

from __future__ import annotations

import threading
from collections.abc import Iterable

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Static

from miniclaude.core.approval import ApprovalDecision, ApprovalRequest
from miniclaude.core.sanitize import sanitize_for_persistence

_MAX_MODAL_TEXT = 4_000


def _display_text(value: object, limit: int = _MAX_MODAL_TEXT) -> str:
    sanitized = sanitize_for_persistence(str(value))
    text = sanitized if isinstance(sanitized, str) else str(sanitized)
    return text[:limit]


class ApprovalGate:
    """Resolve one approval exactly once and allow a worker thread to wait."""

    def __init__(self, request: ApprovalRequest):
        self.request = request
        self._event = threading.Event()
        self._lock = threading.Lock()
        self._decision: ApprovalDecision | None = None

    @property
    def resolved(self) -> bool:
        return self._event.is_set()

    def resolve(self, approved: bool, reason: str = "") -> bool:
        with self._lock:
            if self._decision is not None:
                return False
            self._decision = ApprovalDecision(bool(approved), _display_text(reason, 500))
            self._event.set()
            return True

    def wait(self, timeout: float | None = None) -> ApprovalDecision:
        if not self._event.wait(timeout):
            self.resolve(False, "TUI approval timed out")
        with self._lock:
            return self._decision or ApprovalDecision(
                False,
                "TUI approval ended without a decision",
            )


class ApprovalGateRegistry:
    """Track pending approvals so shutdown can fail every waiter closed."""

    def __init__(self):
        self._lock = threading.Lock()
        self._gates: dict[str, ApprovalGate] = {}

    def create(self, request: ApprovalRequest) -> ApprovalGate:
        gate = ApprovalGate(request)
        with self._lock:
            previous = self._gates.get(request.id)
            if previous is not None:
                previous.resolve(False, "Superseded duplicate approval request")
            self._gates[request.id] = gate
        return gate

    def remove(self, gate: ApprovalGate) -> None:
        with self._lock:
            if self._gates.get(gate.request.id) is gate:
                self._gates.pop(gate.request.id, None)

    def pending(self) -> Iterable[ApprovalGate]:
        with self._lock:
            return tuple(self._gates.values())

    def deny_all(self, reason: str = "TUI closed") -> None:
        with self._lock:
            gates = tuple(self._gates.values())
            self._gates.clear()
        for gate in gates:
            gate.resolve(False, reason)


class ApprovalModal(ModalScreen[bool]):
    """Fail-closed modal for one exact shell command."""

    BINDINGS = [
        ("y", "approve", "Approve"),
        ("n", "deny", "Deny"),
        ("escape", "deny", "Deny"),
        ("enter", "deny", "Deny"),
    ]

    DEFAULT_CSS = """
    ApprovalModal {
        align: center middle;
        background: $background 70%;
    }
    #approval-dialog {
        width: 92%;
        max-width: 90;
        height: auto;
        max-height: 90%;
        padding: 1 2;
        border: round $warning;
        background: $surface;
    }
    #approval-title {
        text-style: bold;
        color: $warning;
        margin-bottom: 1;
    }
    #approval-command {
        height: auto;
        max-height: 12;
        margin: 1 0;
        padding: 1;
        border: solid $primary-darken-1;
        background: $panel;
    }
    #approval-buttons {
        height: auto;
        align-horizontal: right;
    }
    #approval-buttons Button {
        margin-left: 1;
    }
    """

    def __init__(self, gate: ApprovalGate):
        super().__init__()
        self.gate = gate

    def compose(self) -> ComposeResult:
        request = self.gate.request
        workspace = _display_text(request.workspace, 500)
        with Vertical(id="approval-dialog"):
            yield Label("Shell command approval", id="approval-title")
            yield Label(f"Tool: {_display_text(request.tool_name, 100)}")
            yield Label(f"Risk: {_display_text(request.risk_level, 100)}")
            yield Label(f"Reason: {_display_text(request.risk_reason, 1_000)}")
            yield Label(f"Workspace: {workspace}")
            yield Static(_display_text(request.command), id="approval-command")
            yield Label("Only Y or Approve allows this exact command.")
            with Horizontal(id="approval-buttons"):
                yield Button("Deny [Enter]", id="deny", variant="error")
                yield Button("Approve [Y]", id="approve", variant="success")

    def action_approve(self) -> None:
        self.gate.resolve(True, "Approved in TUI")
        self.dismiss(True)

    def action_deny(self) -> None:
        self.gate.resolve(False, "Denied in TUI")
        self.dismiss(False)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "approve":
            self.action_approve()
        else:
            self.action_deny()

    def on_unmount(self) -> None:
        self.gate.resolve(False, "Approval modal closed")
