"""Append-only, sanitized execution tracing for the Stage 5 harness."""

import json
import os
import time
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from miniclaude.core.sanitize import sanitize_for_persistence
from miniclaude.core.state import RuntimeState

MAX_TIMELINE_EVENTS = 1_000
TIMELINE_HEAD = 20
TIMELINE_TAIL = 80


@dataclass
class TraceStats:
    node_visits: dict[str, int] = field(default_factory=dict)
    tool_calls: int = 0
    failed_tool_calls: int = 0
    approval_count: int = 0
    checkpoint_count: int = 0
    handoff_count: int = 0
    compression_count: int = 0


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _atomic_write(path: Path, text: str) -> None:
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(text, encoding="utf-8", newline="\n")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


class TraceRecorder:
    """Persist one immutable run trace without exposing raw exceptions or secrets."""

    def __init__(
        self,
        runtime: RuntimeState,
        task: str = "",
        *,
        clock: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] = _utc_now,
        resumed_from_trace_id: str | None = None,
    ):
        self.runtime = runtime
        self.task = task
        self.configured = runtime.trace_mode == "on"
        self.enabled = self.configured
        self.clock = clock
        self.now = now
        self.resumed_from_trace_id = resumed_from_trace_id
        self.trace_id = self._available_trace_id(runtime.trace_id or uuid4().hex[:12])
        self.root = runtime.workspace / ".miniclaude" / "traces" / self.trace_id
        self.events_path = self.root / "events.jsonl"
        self.sequence = 0
        self.started_at: datetime | None = None
        self.started_clock: float | None = None
        self.timeline: list[str] = []
        self.timeline_omitted = 0
        self.stats = TraceStats()
        self.warning: dict[str, str] | None = None

    def _available_trace_id(self, requested: str) -> str:
        root = self.runtime.workspace / ".miniclaude" / "traces"
        candidate = requested
        suffix = 2
        while (root / candidate).exists():
            candidate = f"{requested}-{suffix}"
            suffix += 1
        return candidate

    def _persistence_failed(self, exc: Exception) -> None:
        self.enabled = False
        if self.warning is None:
            self.warning = {
                "type": "trace_warning",
                "message": f"Trace persistence failed ({type(exc).__name__})",
            }

    def pop_warning(self) -> dict[str, str] | None:
        warning = self.warning
        self.warning = None
        return warning

    def _write_event(self, record: dict[str, object]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        with self.events_path.open("a", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
            stream.write("\n")
            stream.flush()

    def _timeline_entry(self, event_type: str, data: Mapping[str, object]) -> str:
        summary = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
        summary = summary.replace("\r", " ").replace("\n", " ").replace("|", "\\|")
        if len(summary) > 500:
            summary = summary[:489] + "[TRUNCATED]"
        return f"{self.sequence}. {event_type}: {summary}"

    def _append(self, event: Mapping[str, object]) -> None:
        if not self.enabled:
            return
        self.sequence += 1
        event_type = str(event.get("type", "unknown"))
        node = str(event.get("node", ""))
        status = str(event.get("status", ""))
        raw_data = {
            str(key): value for key, value in event.items() if key not in {"type", "node", "status"}
        }
        clean = sanitize_for_persistence(raw_data)
        data = clean if isinstance(clean, dict) else {"value": clean}
        elapsed_ms = 0
        if self.started_clock is not None:
            elapsed_ms = max(0, round((self.clock() - self.started_clock) * 1_000))
        record: dict[str, object] = {
            "sequence": self.sequence,
            "timestamp": self.now().isoformat(),
            "elapsed_ms": elapsed_ms,
            "type": event_type,
            "node": node,
            "status": status,
            "data": data,
        }
        try:
            self._write_event(record)
        except Exception as exc:
            self._persistence_failed(exc)
            return
        entry = self._timeline_entry(event_type, data)
        if len(self.timeline) < MAX_TIMELINE_EVENTS:
            self.timeline.append(entry)
        else:
            self.timeline_omitted += 1

    def start(self, inputs: Mapping[str, object]) -> dict[str, str] | None:
        if not self.configured:
            return None
        self.started_at = self.now()
        self.started_clock = self.clock()
        self._append(
            {
                "type": "run_start",
                "task": self.task,
                "resumed_from_trace_id": self.resumed_from_trace_id or "",
                "input_keys": sorted(str(key) for key in inputs),
            }
        )
        return {"type": "trace_started", "trace_id": self.trace_id}

    def record_custom_event(self, event: Mapping[str, object]) -> None:
        if not self.configured:
            return
        event_type = str(event.get("type", "unknown"))
        if event_type == "tool_call":
            self.stats.tool_calls += 1
        elif event_type == "tool_result":
            result = event.get("result")
            ok = result.get("ok") if isinstance(result, Mapping) else event.get("ok")
            if ok is False:
                self.stats.failed_tool_calls += 1
        elif event_type == "approval_resolved":
            self.stats.approval_count += 1
        elif event_type == "checkpoint_saved":
            self.stats.checkpoint_count += 1
        elif event_type == "handoff":
            self.stats.handoff_count += 1
        elif event_type == "context_compressor":
            self.stats.compression_count += 1
        self._append(event)

    def record_graph_update(self, event: Mapping[str, object]) -> None:
        if not self.configured:
            return
        for node, update in event.items():
            name = str(node)
            self.stats.node_visits[name] = self.stats.node_visits.get(name, 0) + 1
            self._append({"type": "node_update", "node": name, "update": update})

    def _summary(
        self,
        *,
        status: str,
        latest_node: str,
        final_state: Mapping[str, object],
    ) -> dict[str, object]:
        ended_at = self.now()
        duration_ms = 0
        if self.started_clock is not None:
            duration_ms = max(0, round((self.clock() - self.started_clock) * 1_000))
        stats = asdict(self.stats)
        return {
            "trace_id": self.trace_id,
            "task": self.task,
            "status": status,
            "started_at": self.started_at.isoformat() if self.started_at else "",
            "ended_at": ended_at.isoformat(),
            "duration_ms": duration_ms,
            "latest_node": latest_node,
            "passed": bool(final_state.get("passed")),
            **stats,
            "timeline_head": self.timeline[:TIMELINE_HEAD],
            "timeline_tail": self.timeline[-TIMELINE_TAIL:],
            "timeline_omitted": self.timeline_omitted,
            "resumed_from_trace_id": self.resumed_from_trace_id,
        }

    def _write_summary(self, summary: Mapping[str, object]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        clean = sanitize_for_persistence(summary)
        _atomic_write(
            self.root / "trace.json",
            json.dumps(clean, ensure_ascii=False, indent=2) + "\n",
        )
        lines = [
            f"# Trace {self.trace_id}",
            "",
            f"- Status: {summary['status']}",
            f"- Latest node: {summary['latest_node']}",
            f"- Duration: {summary['duration_ms']} ms",
            "",
            "## Timeline",
            "",
            *[f"- {entry}" for entry in self.timeline[:TIMELINE_HEAD]],
        ]
        if len(self.timeline) > TIMELINE_HEAD:
            lines.extend(["", "## Recent Events", ""])
            lines.extend(f"- {entry}" for entry in self.timeline[-TIMELINE_TAIL:])
        _atomic_write(self.root / "timeline.md", "\n".join(lines) + "\n")

    def end(
        self,
        *,
        status: str,
        latest_node: str,
        final_state: Mapping[str, object],
    ) -> dict[str, object] | None:
        if not self.configured:
            return None
        self._append({"type": "run_end", "status": status, "latest_node": latest_node})
        summary = self._summary(
            status=status,
            latest_node=latest_node,
            final_state=final_state,
        )
        if self.enabled:
            try:
                self._write_summary(summary)
            except Exception as exc:
                self._persistence_failed(exc)
        return summary
