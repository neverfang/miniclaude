"""Versioned Stage 5 checkpoint persistence and workspace manifests."""

import hashlib
import json
import os
import subprocess
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage

from miniclaude.core.paths import protected_part
from miniclaude.core.sanitize import sanitize_for_persistence
from miniclaude.core.snapshot import WorkspaceSnapshotStore
from miniclaude.core.state import RuntimeState

CHECKPOINT_FORMAT_VERSION = 1
CHECKPOINT_FILE = "checkpoint.json"
STATE_FILE = "state.json"
EVENTS_FILE = "events.jsonl"
RECOVERY_FILE = "RECOVERY.md"
MAX_MANIFEST_FILES = 5_000
MAX_SNAPSHOT_FILE_BYTES = 5 * 1024 * 1024

_RESUME_FIELDS = (
    "task",
    "messages",
    "plan_summary",
    "todos",
    "acceptance_criteria",
    "verification_commands",
    "verification_results",
    "verification_checks",
    "passed",
    "verification_reason",
    "last_error",
    "attempts",
    "max_attempts",
    "last_actor_summary",
    "final_answer",
    "research_notes",
    "sources",
    "agent_handoffs",
    "code_agent_summary",
    "supervisor_summary",
    "context_summary",
    "context_token_count",
    "context_token_limit",
    "context_should_compress",
    "context_next_node",
    "context_error",
    "context_count_method",
    "compression_events",
    "memory_snapshot",
    "history_summary",
)


def _atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(text, encoding="utf-8", newline="\n")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _append_json_line(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    clean = sanitize_for_persistence(value)
    with path.open("a", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(clean, ensure_ascii=False, separators=(",", ":")))
        stream.write("\n")
        stream.flush()


def workspace_identity(workspace: Path) -> str:
    normalized = str(Path(workspace).resolve()).casefold().encode("utf-8")
    return hashlib.sha256(normalized).hexdigest()


def _serialize_message(message: BaseMessage) -> dict[str, object]:
    common: dict[str, object] = {
        "content": sanitize_for_persistence(message.content),
        "id": message.id,
    }
    if isinstance(message, HumanMessage):
        return {"type": "human", **common}
    if isinstance(message, SystemMessage):
        return {"type": "system", **common}
    if isinstance(message, AIMessage):
        return {
            "type": "ai",
            **common,
            "tool_calls": sanitize_for_persistence(message.tool_calls),
        }
    if isinstance(message, ToolMessage):
        return {
            "type": "tool",
            **common,
            "tool_call_id": message.tool_call_id,
            "name": message.name,
        }
    return {"type": "unsupported", "class": type(message).__name__}


def serialize_resume_state(state: Mapping[str, object]) -> dict[str, object]:
    serialized: dict[str, object] = {}
    for field in _RESUME_FIELDS:
        if field not in state:
            continue
        value = state[field]
        if field == "messages":
            messages = value if isinstance(value, list | tuple) else []
            serialized[field] = [
                _serialize_message(message)
                for message in messages
                if isinstance(message, BaseMessage)
            ]
        else:
            serialized[field] = sanitize_for_persistence(value)
    return serialized


def workspace_manifest(workspace: Path) -> dict[str, object]:
    root = Path(workspace).resolve()
    files: list[dict[str, object]] = []
    excluded: dict[str, int] = {}
    truncated = False

    def exclude(reason: str) -> None:
        excluded[reason] = excluded.get(reason, 0) + 1

    for directory, names, filenames in os.walk(root, followlinks=False):
        current = Path(directory)
        allowed_names = []
        for name in sorted(names):
            candidate = current / name
            if protected_part(name) or candidate.is_symlink():
                exclude("protected_or_linked_directory")
            else:
                allowed_names.append(name)
        names[:] = allowed_names
        for name in sorted(filenames):
            if len(files) >= MAX_MANIFEST_FILES:
                truncated = True
                break
            candidate = current / name
            if protected_part(name) or candidate.is_symlink() or not candidate.is_file():
                exclude("protected_linked_or_nonregular_file")
                continue
            try:
                size = candidate.stat().st_size
            except OSError:
                exclude("unreadable_file")
                continue
            if size > MAX_SNAPSHOT_FILE_BYTES:
                exclude("oversized_file")
                continue
            digest = hashlib.sha256()
            try:
                with candidate.open("rb") as stream:
                    for chunk in iter(lambda: stream.read(64 * 1024), b""):
                        digest.update(chunk)
            except OSError:
                exclude("unreadable_file")
                continue
            files.append(
                {
                    "path": candidate.relative_to(root).as_posix(),
                    "size": size,
                    "mtime_ns": candidate.stat().st_mtime_ns,
                    "sha256": digest.hexdigest(),
                }
            )
        if truncated:
            break
    return {"files": files, "excluded": excluded, "truncated": truncated}


def build_recovery_markdown(payload: Mapping[str, object]) -> str:
    state = payload.get("state")
    attempts = state.get("attempts", 0) if isinstance(state, Mapping) else 0
    return "\n".join(
        [
            "# miniclaude Recovery",
            "",
            f"- Status: {payload.get('status', 'unknown')}",
            f"- Last safe node: {payload.get('latest_node', 'unknown')}",
            f"- Attempts: {attempts}",
            f"- Snapshot restorable: {bool(payload.get('snapshot_restorable'))}",
            "",
            "Resume with the files currently on disk:",
            "",
            "```text",
            "miniclaude --resume .",
            "```",
            "",
            "Explicitly restore eligible checkpoint files first:",
            "",
            "```text",
            "miniclaude --resume . --restore-workspace",
            "```",
            "",
        ]
    )


class CheckpointManager:
    """Persist a bounded semantic checkpoint at safe workflow boundaries."""

    def __init__(self, runtime: RuntimeState, task: str = ""):
        self.runtime = runtime
        self.workspace = runtime.workspace
        self.task = task
        self.mode = runtime.checkpoint_mode
        self.root = self.workspace / ".miniclaude" / "checkpoints"
        self.snapshot_git_dir = self.root / "snapshot.git"
        self._snapshot_store = WorkspaceSnapshotStore(
            runtime,
            self.snapshot_git_dir,
            lambda *args, **kwargs: self._run_git(*args, **kwargs),
        )

    @property
    def enabled(self) -> bool:
        return self.mode != "off"

    def _run_git(
        self,
        *args: str,
        use_repo: bool = True,
    ) -> subprocess.CompletedProcess[bytes]:
        command = ["git"]
        if use_repo:
            command.extend(
                [
                    f"--git-dir={self.snapshot_git_dir}",
                    f"--work-tree={self.workspace}",
                ]
            )
        command.extend(args)
        return subprocess.run(
            command,
            cwd=self.workspace,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            timeout=30,
            check=False,
        )

    def _snapshot_workspace(self, manifest: Mapping[str, object]) -> dict[str, object]:
        try:
            return self._snapshot_store.save(manifest)
        except Exception as exc:
            return {
                "commit": "",
                "restorable": False,
                "error": f"Workspace snapshot failed ({type(exc).__name__})",
            }

    def restore_workspace(self, commit: str) -> dict[str, object]:
        manifest = workspace_manifest(self.workspace)
        return self._snapshot_store.restore(commit, manifest)

    def save(
        self,
        state: Mapping[str, object],
        *,
        status: str = "running",
        latest_node: str | None = None,
        event: Mapping[str, object] | None = None,
    ) -> dict[str, object] | None:
        if not self.enabled:
            return None

        manifest = workspace_manifest(self.workspace)
        snapshot = self._snapshot_workspace(manifest)
        payload: dict[str, object] = {
            "format_version": CHECKPOINT_FORMAT_VERSION,
            "checkpoint_id": uuid4().hex[:12],
            "workspace_id": workspace_identity(self.workspace),
            "task": self.task,
            "status": status,
            "latest_node": latest_node or "unknown",
            "resume_node": "contextual_supervisor",
            "saved_at": datetime.now(UTC).isoformat(),
            "trace_id": self.runtime.trace_id,
            "snapshot_commit": snapshot["commit"],
            "snapshot_restorable": snapshot["restorable"],
            "snapshot_error": snapshot["error"],
            "state": serialize_resume_state(state),
            "manifest": manifest,
        }
        clean = sanitize_for_persistence(payload)
        _atomic_write_text(
            self.root / CHECKPOINT_FILE,
            json.dumps(clean, ensure_ascii=False, indent=2) + "\n",
        )
        _atomic_write_text(self.root / RECOVERY_FILE, build_recovery_markdown(payload))
        if self.mode == "strict":
            _atomic_write_text(
                self.root / STATE_FILE,
                json.dumps(serialize_resume_state(state), ensure_ascii=False, indent=2) + "\n",
            )
            if event is not None:
                _append_json_line(self.root / EVENTS_FILE, event)

        return {
            "type": "checkpoint_saved",
            "checkpoint_id": payload["checkpoint_id"],
            "status": status,
            "latest_node": payload["latest_node"],
            "mode": self.mode,
            "path": f".miniclaude/checkpoints/{CHECKPOINT_FILE}",
            "file_count": len(manifest["files"]),
            "snapshot_commit": snapshot["commit"],
            "snapshot_restorable": snapshot["restorable"],
            "snapshot_error": snapshot["error"],
        }

    @staticmethod
    def _normalized_task(value: str) -> str:
        return " ".join(value.split())

    @staticmethod
    def _resume_message(record: object) -> BaseMessage:
        if not isinstance(record, Mapping):
            raise ValueError("Invalid checkpoint message record")
        kind = record.get("type")
        content = record.get("content", "")
        message_id = record.get("id")
        if not isinstance(content, (str, list)):
            raise ValueError("Invalid checkpoint message content")
        if message_id is not None and not isinstance(message_id, str):
            raise ValueError("Invalid checkpoint message id")
        try:
            if kind == "human":
                return HumanMessage(content=content, id=message_id)
            if kind == "system":
                return SystemMessage(content=content, id=message_id)
            if kind == "ai":
                tool_calls = record.get("tool_calls", [])
                if not isinstance(tool_calls, list):
                    raise ValueError("Invalid checkpoint message tool calls")
                return AIMessage(content=content, id=message_id, tool_calls=tool_calls)
            if kind == "tool":
                tool_call_id = record.get("tool_call_id")
                name = record.get("name")
                if not isinstance(tool_call_id, str) or not tool_call_id:
                    raise ValueError("Invalid checkpoint tool message id")
                if name is not None and not isinstance(name, str):
                    raise ValueError("Invalid checkpoint tool message name")
                return ToolMessage(
                    content=content,
                    id=message_id,
                    tool_call_id=tool_call_id,
                    name=name,
                )
        except ValueError:
            raise
        except Exception as exc:
            raise ValueError(f"Invalid checkpoint message ({type(exc).__name__})") from None
        raise ValueError("Unsupported checkpoint message type")

    @staticmethod
    def _manifest_drift(
        saved_manifest: object,
        current_manifest: Mapping[str, object],
    ) -> dict[str, int]:
        if not isinstance(saved_manifest, Mapping):
            raise ValueError("Invalid checkpoint manifest")
        saved_files = saved_manifest.get("files")
        current_files = current_manifest.get("files")
        if not isinstance(saved_files, list) or not isinstance(current_files, list):
            raise ValueError("Invalid checkpoint manifest files")

        def indexed(files: list[object]) -> dict[str, str]:
            result: dict[str, str] = {}
            for entry in files:
                if not isinstance(entry, Mapping):
                    raise ValueError("Invalid checkpoint manifest entry")
                path = entry.get("path")
                digest = entry.get("sha256")
                if not isinstance(path, str) or not isinstance(digest, str):
                    raise ValueError("Invalid checkpoint manifest entry")
                result[path] = digest
            return result

        saved = indexed(saved_files)
        current = indexed(current_files)
        shared = saved.keys() & current.keys()
        return {
            "added": len(current.keys() - saved.keys()),
            "removed": len(saved.keys() - current.keys()),
            "modified": sum(saved[path] != current[path] for path in shared),
        }

    def load_resume_inputs(
        self,
        runtime: RuntimeState,
        *,
        task: str | None = None,
        restore_workspace: bool = False,
    ) -> tuple[dict[str, object], dict[str, object]]:
        """Validate a checkpoint and rebuild fresh graph inputs for safe resume."""
        path = self.root / CHECKPOINT_FILE
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"Invalid checkpoint data ({type(exc).__name__})") from None
        if not isinstance(payload, Mapping):
            raise ValueError("Invalid checkpoint schema")
        version = payload.get("format_version")
        if not isinstance(version, int) or isinstance(version, bool):
            raise ValueError("Invalid checkpoint version")
        if version != CHECKPOINT_FORMAT_VERSION:
            raise ValueError("Unsupported checkpoint version")
        if payload.get("workspace_id") != workspace_identity(runtime.workspace):
            raise ValueError("Checkpoint workspace does not match")
        if payload.get("resume_node") != "contextual_supervisor":
            raise ValueError("Invalid checkpoint resume node")

        stored_task = payload.get("task")
        stored_state = payload.get("state")
        if not isinstance(stored_task, str) or not stored_task.strip():
            raise ValueError("Invalid checkpoint task")
        if not isinstance(stored_state, Mapping):
            raise ValueError("Invalid checkpoint state")
        state_task = stored_state.get("task")
        if not isinstance(state_task, str):
            raise ValueError("Invalid checkpoint state task")
        normalized_task = self._normalized_task(stored_task)
        if self._normalized_task(state_task) != normalized_task:
            raise ValueError("Checkpoint task mismatch")
        requested_task = task if task is not None else self.task
        if requested_task and self._normalized_task(requested_task) != normalized_task:
            raise ValueError("Checkpoint task mismatch")

        max_attempts = stored_state.get("max_attempts", 3)
        attempts = stored_state.get("attempts", 0)
        if (
            not isinstance(max_attempts, int)
            or isinstance(max_attempts, bool)
            or not 1 <= max_attempts <= 10
        ):
            raise ValueError("Invalid checkpoint maximum attempts")
        if (
            not isinstance(attempts, int)
            or isinstance(attempts, bool)
            or not 0 <= attempts <= max_attempts
        ):
            raise ValueError("Invalid checkpoint attempts")

        string_fields = {
            "plan_summary",
            "verification_reason",
            "last_error",
            "last_actor_summary",
            "final_answer",
            "research_notes",
            "code_agent_summary",
            "supervisor_summary",
            "context_summary",
            "context_next_node",
            "context_error",
            "context_count_method",
            "history_summary",
        }
        bool_fields = {"passed", "context_should_compress"}
        int_fields = {"context_token_count", "context_token_limit"}
        string_list_fields = {"acceptance_criteria", "verification_commands"}
        mapping_list_fields = {
            "todos",
            "verification_results",
            "verification_checks",
            "sources",
            "agent_handoffs",
            "compression_events",
        }
        mapping_fields = {"memory_snapshot"}

        from miniclaude.graph.state import initial_graph_state

        inputs: dict[str, object] = dict(
            initial_graph_state(
                normalized_task,
                runtime=runtime,
                max_attempts=max_attempts,
            )
        )
        inputs["attempts"] = attempts
        for field in string_fields:
            if field not in stored_state:
                continue
            value = stored_state[field]
            if not isinstance(value, str):
                raise ValueError(f"Invalid checkpoint state field: {field}")
            inputs[field] = value
        for field in bool_fields:
            if field not in stored_state:
                continue
            value = stored_state[field]
            if not isinstance(value, bool):
                raise ValueError(f"Invalid checkpoint state field: {field}")
            inputs[field] = value
        for field in int_fields:
            if field not in stored_state:
                continue
            value = stored_state[field]
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError(f"Invalid checkpoint state field: {field}")
            inputs[field] = value
        for field in string_list_fields:
            if field not in stored_state:
                continue
            value = stored_state[field]
            if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
                raise ValueError(f"Invalid checkpoint state field: {field}")
            inputs[field] = json.loads(json.dumps(value))
        for field in mapping_list_fields:
            if field not in stored_state:
                continue
            value = stored_state[field]
            if not isinstance(value, list) or not all(isinstance(item, Mapping) for item in value):
                raise ValueError(f"Invalid checkpoint state field: {field}")
            inputs[field] = json.loads(json.dumps(value))
        for field in mapping_fields:
            if field not in stored_state:
                continue
            value = stored_state[field]
            if not isinstance(value, Mapping):
                raise ValueError(f"Invalid checkpoint state field: {field}")
            inputs[field] = json.loads(json.dumps(value))

        messages = stored_state.get("messages", [])
        if not isinstance(messages, list):
            raise ValueError("Invalid checkpoint messages")
        inputs["messages"] = [self._resume_message(record) for record in messages]

        restore_event = None
        if restore_workspace:
            snapshot_commit = payload.get("snapshot_commit")
            if not isinstance(snapshot_commit, str) or not snapshot_commit:
                raise ValueError("Checkpoint has no restorable snapshot commit")
            restore_event = self.restore_workspace(snapshot_commit)

        current_manifest = workspace_manifest(runtime.workspace)
        drift = self._manifest_drift(payload.get("manifest"), current_manifest)
        inputs["context_next_node"] = "verifier"
        inputs["context_should_compress"] = False
        inputs["context_error"] = ""
        inputs["passed"] = False
        inputs["final_answer"] = ""

        event: dict[str, object] = {
            "type": "resume_loaded",
            "checkpoint_id": payload.get("checkpoint_id", ""),
            "latest_node": payload.get("latest_node", "unknown"),
            "resume_node": "contextual_supervisor",
            "previous_trace_id": payload.get("trace_id"),
            "workspace_drift": any(drift.values()),
            "drift": drift,
        }
        if restore_event is not None:
            event["restore"] = restore_event
        return inputs, event
