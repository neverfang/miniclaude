"""Lifecycle coordination for Stage 5 checkpoints, traces, and approvals."""

from collections.abc import Mapping

from miniclaude.core.checkpoint import CheckpointManager
from miniclaude.core.state import RuntimeState
from miniclaude.core.trace import TraceRecorder


class HarnessRunner:
    """Coordinate persistence around a workflow without owning its graph logic."""

    def __init__(
        self,
        runtime: RuntimeState,
        task: str,
        *,
        checkpoint: CheckpointManager | None = None,
        trace: TraceRecorder | None = None,
        resumed_from_trace_id: str | None = None,
    ):
        self.runtime = runtime
        self.task = task
        self.checkpoint = checkpoint or CheckpointManager(runtime, task)
        self.trace = trace or TraceRecorder(
            runtime, task, resumed_from_trace_id=resumed_from_trace_id
        )
        self.runtime.trace_id = getattr(self.trace, "trace_id", runtime.trace_id)
        self.runtime.event_handler = self.record_runtime_event
        self._state: Mapping[str, object] = {}
        self._runtime_events: list[dict[str, object]] = []
        self._finished = False

    @staticmethod
    def _warning(kind: str, exc: Exception) -> dict[str, str]:
        label = "Trace" if kind == "trace_warning" else "Checkpoint"
        return {
            "type": kind,
            "message": f"{label} persistence failed ({type(exc).__name__})",
        }

    def _trace_warning(self) -> dict[str, object] | None:
        try:
            return self.trace.pop_warning()
        except Exception as exc:
            return self._warning("trace_warning", exc)

    def _record_trace_event(self, event: Mapping[str, object]) -> list[dict[str, object]]:
        try:
            self.trace.record_custom_event(event)
        except Exception as exc:
            return [self._warning("trace_warning", exc)]
        warning = self._trace_warning()
        return [warning] if warning is not None else []

    def _save_checkpoint(
        self,
        *,
        status: str,
        latest_node: str,
        event: Mapping[str, object] | None = None,
    ) -> list[dict[str, object]]:
        try:
            saved = self.checkpoint.save(
                self._state,
                status=status,
                latest_node=latest_node,
                event=event,
            )
        except Exception as exc:
            return [self._warning("checkpoint_warning", exc)]
        if saved is None:
            return []
        events = [saved]
        events.extend(self._record_trace_event(saved))
        return events

    def start(self, state: Mapping[str, object]) -> list[dict[str, object]]:
        self._state = state
        events: list[dict[str, object]] = []
        try:
            started = self.trace.start(state)
        except Exception as exc:
            events.append(self._warning("trace_warning", exc))
        else:
            if started is not None:
                events.append(started)
            warning = self._trace_warning()
            if warning is not None:
                events.append(warning)
        events.extend(self._save_checkpoint(status="started", latest_node="start"))
        return events

    def record_custom_event(
        self,
        event: Mapping[str, object],
        state: Mapping[str, object] | None = None,
    ) -> list[dict[str, object]]:
        if state is not None:
            self._state = state
        events = self._record_trace_event(event)
        if event.get("type") in {
            "approval_requested",
            "approval_resolved",
            "context_compressor",
            "error",
        }:
            node = "approval" if str(event.get("type", "")).startswith("approval_") else "event"
            events.extend(self._save_checkpoint(status="running", latest_node=node, event=event))
        return events

    def record_runtime_event(self, event: dict[str, object]) -> None:
        self._runtime_events.append(event)
        self._runtime_events.extend(self.record_custom_event(event))

    def drain_runtime_events(self) -> list[dict[str, object]]:
        events = self._runtime_events
        self._runtime_events = []
        return events

    def record_graph_update(
        self,
        node: str,
        update: Mapping[str, object],
        state: Mapping[str, object],
    ) -> list[dict[str, object]]:
        self._state = state
        events: list[dict[str, object]] = []
        graph_event = {node: update}
        try:
            self.trace.record_graph_update(graph_event)
        except Exception as exc:
            events.append(self._warning("trace_warning", exc))
        else:
            warning = self._trace_warning()
            if warning is not None:
                events.append(warning)
        events.extend(self._save_checkpoint(status="running", latest_node=node, event=graph_event))
        return events

    def finish(
        self,
        *,
        status: str,
        latest_node: str,
        state: Mapping[str, object],
    ) -> dict[str, object] | None:
        if self._finished:
            return None
        self._finished = True
        self._state = state
        self._runtime_events.extend(self._save_checkpoint(status=status, latest_node=latest_node))
        try:
            summary = self.trace.end(
                status=status,
                latest_node=latest_node,
                final_state=state,
            )
        except Exception as exc:
            self._runtime_events.append(self._warning("trace_warning", exc))
            return None
        warning = self._trace_warning()
        if warning is not None:
            self._runtime_events.append(warning)
        if summary is None:
            return None
        return {"type": "trace_summary", **summary}

    def interrupt(self, *, latest_node: str, state: Mapping[str, object]) -> None:
        self.finish(status="interrupted", latest_node=latest_node, state=state)

    def fail(self, *, latest_node: str, state: Mapping[str, object]) -> None:
        self.finish(status="failed", latest_node=latest_node, state=state)


__all__ = ["HarnessRunner"]
