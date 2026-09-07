"""Textual application for persistent Stage 6 Sessions."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path

from textual import events, work
from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.widgets import Button, Footer, Header, Input
from textual.worker import get_current_worker

from miniclaude.cli.tui.approval import (
    ApprovalGate,
    ApprovalGateRegistry,
    ApprovalModal,
)
from miniclaude.cli.tui.state import (
    SessionViewState,
    initial_session_view,
    reduce_session_event,
)
from miniclaude.cli.tui.widgets import (
    CommandSuggestions,
    ConversationPanel,
    EventStream,
    PlanPanel,
    SessionSidebar,
)
from miniclaude.commands.registry import CommandContext, build_command_registry
from miniclaude.core.approval import ApprovalDecision, ApprovalRequest
from miniclaude.core.session import (
    SessionData,
    create_session,
    load_latest_session,
    load_session,
    save_session,
)
from miniclaude.core.session_controller import stream_session_turn
from miniclaude.skills.catalog import discover_skills


class AgentEventMessage(Message):
    def __init__(self, event: dict):
        super().__init__()
        self.event = event


class TurnCompletedMessage(Message):
    pass


class ApprovalRequestedMessage(Message):
    def __init__(self, gate: ApprovalGate):
        super().__init__()
        self.gate = gate


class MiniclaudeTuiApp(App):
    TITLE = "Miniclaude"
    SUB_TITLE = "coding Session with context + harness"
    CSS_PATH = "app.tcss"
    BINDINGS = [
        ("ctrl+c", "cancel_or_quit", "Cancel / quit"),
        ("ctrl+l", "clear_visuals", "Clear view"),
        ("ctrl+n", "new_session", "New Session"),
        ("ctrl+o", "show_workspace", "Workspace"),
        ("ctrl+s", "toggle_plan", "Plan"),
    ]

    def __init__(
        self,
        *,
        session: SessionData,
        startup_directory: Path,
        model: object | None = None,
        turn_stream: Callable[..., Iterator[dict]] = stream_session_turn,
        workflow_options: dict[str, object] | None = None,
    ):
        super().__init__()
        self.session = session
        self.startup_directory = Path(startup_directory).resolve()
        self.model = model
        self.turn_stream = turn_stream
        self.workflow_options = dict(workflow_options or {})
        self.gate_registry = ApprovalGateRegistry()
        self.command_registry = build_command_registry()
        self.view_state: SessionViewState = initial_session_view(
            session["session_id"],
            session["workspace"],
            turns=session["turn_index"],
        )
        self._turn_active = False
        self._active_worker = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal(id="body"):
            with Vertical(id="execution-column"):
                yield PlanPanel(id="plan")
                yield EventStream(id="event-stream")
                yield ConversationPanel(id="conversation")
            yield SessionSidebar(id="session-sidebar")
        yield CommandSuggestions(id="command-suggestions")
        with Horizontal(id="prompt-row"):
            yield Input(
                placeholder="Chat or ask for coding work, then press Enter",
                id="prompt",
            )
            yield Button("Send", id="send", variant="primary")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one(PlanPanel).update_plan([])
        conversation = self.query_one(ConversationPanel)
        if self.session["recent_turns"]:
            for item in self.session["recent_turns"]:
                content = str(item.get("content", ""))
                if item.get("role") == "user":
                    conversation.append_user(content)
                elif item.get("role") == "assistant":
                    conversation.append_assistant(content)
        self.query_one(SessionSidebar).update_state(self.view_state)
        self.query_one("#prompt", Input).focus()

    def on_resize(self, event: events.Resize) -> None:
        self.screen.set_class(event.size.width < 90, "narrow")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "prompt":
            self.submit_prompt()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != "prompt":
            return
        self.query_one(CommandSuggestions).show_suggestions(
            self.command_registry.suggest(event.value)
        )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "send":
            self.submit_prompt()

    def submit_prompt(self) -> None:
        prompt = self.query_one("#prompt", Input)
        task = prompt.value.strip()
        if not task or self._turn_active:
            return
        if task.startswith(("/", "／")):
            prompt.value = ""
            self._run_command(task)
            return
        self._turn_active = True
        prompt.value = ""
        prompt.disabled = True
        self.query_one(ConversationPanel).append_user(task)
        self._active_worker = self._run_turn(task)

    def _command_context(self) -> CommandContext:
        skills = discover_skills(self.startup_directory)
        return CommandContext(
            session_id=self.session["session_id"],
            workspace=self.session["workspace"],
            status=self.view_state.status,
            route=self.view_state.route,
            turn_active=self._turn_active,
            allow_shell=bool(self.workflow_options.get("allow_shell", False)),
            approval_mode=str(self.workflow_options.get("approval_mode", "inline")),
            tool_names=(
                "FileReadTool",
                "FileWriteTool",
                "FileEditTool",
                "GrepTool",
                "BashTool",
            ),
            skill_names=tuple(skill.name for skill in skills),
            active_skill=self.session.get("active_skill", ""),
        )

    def _run_command(self, raw: str) -> None:
        result = self.command_registry.execute(raw, self._command_context())
        if result.ok:
            if result.action == "new":
                self.action_new_session()
            elif result.action == "clear":
                self.action_clear_visuals()
            elif result.action == "plan":
                self.action_toggle_plan()
            elif result.action and result.action.startswith("skill:"):
                selected = result.action.partition(":")[2]
                self.session["active_skill"] = "" if selected == "off" else selected
                save_session(self.startup_directory, self.session)
            elif result.action == "exit":
                self.exit()
                return
        self.query_one(EventStream).append_event(
            {
                "type": "command_result",
                "command": raw,
                "ok": result.ok,
                "message": result.message,
            }
        )

    def _approval_handler(self, request: ApprovalRequest) -> ApprovalDecision:
        gate = self.gate_registry.create(request)
        self.call_from_thread(self.post_message, ApprovalRequestedMessage(gate))
        decision = gate.wait()
        self.gate_registry.remove(gate)
        self.call_from_thread(
            self.post_message,
            AgentEventMessage(
                {
                    "type": "approval_resolved",
                    "approval_id": request.id,
                    "approved": decision.approved,
                }
            ),
        )
        return decision

    @work(thread=True, exclusive=True, group="session-turn", exit_on_error=False)
    def _run_turn(self, task: str) -> None:
        worker = get_current_worker()
        options = dict(self.workflow_options)
        options["approval_handler"] = self._approval_handler
        try:
            events_source = self.turn_stream(
                task,
                session=self.session,
                startup_directory=self.startup_directory,
                model=self.model,
                workflow_options=options,
            )
            for event in events_source:
                if worker.is_cancelled:
                    break
                self.call_from_thread(
                    self.post_message,
                    AgentEventMessage(event),
                )
        finally:
            self.call_from_thread(self.post_message, TurnCompletedMessage())

    def on_agent_event_message(self, message: AgentEventMessage) -> None:
        event = message.event
        if event.get("_skip_render"):
            return
        self.view_state = reduce_session_event(self.view_state, event)
        self.query_one(SessionSidebar).update_state(self.view_state)
        kind = event.get("type")
        if kind in {"planner", "supervisor", "actor"}:
            self.query_one(PlanPanel).update_plan(event.get("todos"))
        if kind == "session_final":
            self.query_one(ConversationPanel).append_assistant(
                str(event.get("content", ""))
            )
        elif kind == "session_error":
            self.query_one(ConversationPanel).append_assistant(
                str(event.get("message", "Session turn failed"))
            )
        elif kind not in {"session_status", "intent_decision"}:
            self.query_one(EventStream).append_event(event)

    def on_turn_completed_message(self, message: TurnCompletedMessage) -> None:
        self._turn_active = False
        self._active_worker = None
        prompt = self.query_one("#prompt", Input)
        prompt.disabled = False
        prompt.focus()

    def on_approval_requested_message(
        self,
        message: ApprovalRequestedMessage,
    ) -> None:
        event = {
            "type": "approval_requested",
            "approval_id": message.gate.request.id,
            "risk_level": message.gate.request.risk_level,
            "risk_reason": message.gate.request.risk_reason,
            "command": message.gate.request.command,
        }
        self.on_agent_event_message(AgentEventMessage(event))
        self.push_screen(ApprovalModal(message.gate))

    def action_cancel_or_quit(self) -> None:
        if self._turn_active:
            self.gate_registry.deny_all("Session turn cancelled")
            if self._active_worker is not None:
                self._active_worker.cancel()
            self.view_state = reduce_session_event(
                self.view_state,
                {"type": "session_cancelled"},
            )
            self.query_one(SessionSidebar).update_state(self.view_state)
            return
        self.exit()

    def action_clear_visuals(self) -> None:
        self.query_one(EventStream).clear_events()
        self.query_one(ConversationPanel).clear_conversation()

    def action_new_session(self) -> None:
        if self._turn_active:
            return
        self.session = create_session(self.startup_directory)
        self.view_state = initial_session_view(
            self.session["session_id"],
            self.session["workspace"],
        )
        self.query_one(SessionSidebar).update_state(self.view_state)
        self.action_clear_visuals()
        self.query_one(PlanPanel).update_plan([])

    def action_show_workspace(self) -> None:
        self.query_one(EventStream).append_event(
            {
                "type": "workspace",
                "path": str(self.session["workspace"]),
            }
        )

    def action_toggle_plan(self) -> None:
        self.query_one(PlanPanel).toggle_class("collapsed")

    def on_unmount(self) -> None:
        self.gate_registry.deny_all("TUI closed")


MiniclaudeTuiApp._approval_handler._renders_approval_events = True


def launch_tui(
    *,
    startup_directory: Path,
    selection: str,
    session_id: str | None = None,
    model: object | None = None,
    workflow_options: dict[str, object] | None = None,
) -> None:
    """Select a Session and run the local Textual application."""

    root = Path(startup_directory).resolve()
    if selection == "new":
        session = create_session(root)
    elif selection == "latest":
        session = load_latest_session(root)
    elif selection == "explicit" and session_id is not None:
        session = load_session(root, session_id)
    else:
        raise ValueError("Invalid TUI Session selection")
    MiniclaudeTuiApp(
        session=session,
        startup_directory=root,
        model=model,
        workflow_options=workflow_options,
    ).run()
