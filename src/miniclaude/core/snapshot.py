"""Isolated Git object storage for recoverable workspace snapshots."""

import os
import re
import subprocess
from collections.abc import Callable, Mapping
from pathlib import Path
from uuid import uuid4

from miniclaude.core.paths import workspace_path
from miniclaude.core.state import RuntimeState

GitRunner = Callable[..., subprocess.CompletedProcess[bytes]]
_COMMIT_ID = re.compile(r"[0-9a-f]{40,64}")


def _atomic_write_bytes(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_bytes(content)
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


class WorkspaceSnapshotStore:
    """Commit eligible workspace files without touching a user's Git repository."""

    def __init__(self, runtime: RuntimeState, git_dir: Path, runner: GitRunner):
        self.runtime = runtime
        self.workspace = runtime.workspace
        self.git_dir = git_dir
        self.runner = runner

    def _output(self, *args: str, allow_failure: bool = False) -> bytes:
        completed = self.runner(*args)
        if completed.returncode and not allow_failure:
            raise RuntimeError("snapshot Git command failed")
        return completed.stdout

    def _initialize(self) -> None:
        if (self.git_dir / "HEAD").exists():
            return
        completed = self.runner("init", "--bare", str(self.git_dir), use_repo=False)
        if completed.returncode:
            raise RuntimeError("snapshot Git initialization failed")
        for key, value in (
            ("user.name", "miniclaude checkpoint"),
            ("user.email", "checkpoint@miniclaude.invalid"),
        ):
            self._output("config", key, value)
        self._output("symbolic-ref", "HEAD", "refs/heads/main")

    def save(self, manifest: Mapping[str, object]) -> dict[str, object]:
        self._initialize()
        files = manifest.get("files", [])
        paths = [
            str(entry["path"])
            for entry in files
            if isinstance(entry, Mapping) and isinstance(entry.get("path"), str)
        ]
        for path in paths:
            workspace_path(self.runtime, path)

        self._output("read-tree", "--empty")
        pathspec = self.git_dir.parent / ".snapshot-paths"
        try:
            pathspec.write_bytes(b"".join(path.encode("utf-8") + b"\0" for path in paths))
            if paths:
                self._output(
                    "add",
                    f"--pathspec-from-file={pathspec}",
                    "--pathspec-file-nul",
                )
        finally:
            if pathspec.exists():
                pathspec.unlink()

        tree = self._output("write-tree").decode("ascii").strip()
        parent_result = self.runner("rev-parse", "--verify", "HEAD")
        parent = (
            parent_result.stdout.decode("ascii").strip() if not parent_result.returncode else ""
        )
        if parent:
            old_tree = self._output("rev-parse", f"{parent}^{{tree}}").decode("ascii").strip()
            if tree == old_tree:
                return {"commit": parent, "restorable": True, "error": ""}

        arguments = ["commit-tree", tree, "-m", "miniclaude checkpoint"]
        if parent:
            arguments[2:2] = ["-p", parent]
        commit = self._output(*arguments).decode("ascii").strip()
        self._output("update-ref", "refs/heads/main", commit)
        return {"commit": commit, "restorable": True, "error": ""}

    def validate_commit(self, commit: str) -> None:
        if not _COMMIT_ID.fullmatch(commit):
            raise ValueError("invalid snapshot commit")
        completed = self.runner("cat-file", "-e", f"{commit}^{{commit}}")
        if completed.returncode:
            raise ValueError("unknown snapshot commit")

    def restore(self, commit: str, manifest: Mapping[str, object]) -> dict[str, object]:
        self.validate_commit(commit)
        backup = self.save(manifest)
        if not backup.get("restorable") or not backup.get("commit"):
            raise RuntimeError("pre-restore workspace snapshot failed")

        listing = self._output("ls-tree", "-r", "--name-only", "-z", commit)
        decoded_paths = [
            item.decode("utf-8", errors="strict") for item in listing.split(b"\0") if item
        ]
        targets = [(path, workspace_path(self.runtime, path)) for path in decoded_paths]
        for relative, target in targets:
            content = self._output("show", f"{commit}:{relative}")
            _atomic_write_bytes(target, content)
        return {
            "type": "restore_completed",
            "target_commit": commit,
            "backup_commit": backup["commit"],
            "restored_files": len(targets),
        }
