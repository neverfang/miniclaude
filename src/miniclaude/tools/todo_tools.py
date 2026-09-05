"""In-memory Todo tools used by the stage-two planner and actor."""

from copy import deepcopy
from typing import cast

from langchain_core.tools import StructuredTool

from miniclaude.core.state import ToolError
from miniclaude.graph.state import TodoItem, TodoStatus

VALID_STATUSES = {"pending", "in_progress", "completed", "blocked"}


class TodoTracker:
    """Own a defensive, validated copy of the current execution plan."""

    def __init__(self, todos: list[TodoItem] | None = None):
        self._todos: list[TodoItem] = []
        if todos is not None:
            self.write(todos)

    def write(self, todos: list[dict]) -> dict:
        if not todos:
            raise ToolError("Todo plan must contain at least one item")
        normalized: list[TodoItem] = []
        ids: set[str] = set()
        for raw in todos:
            todo_id = str(raw.get("id", "")).strip()
            content = str(raw.get("content", "")).strip()
            status = str(raw.get("status", "pending")).strip()
            note = str(raw.get("note", "")).strip()
            if not todo_id:
                raise ToolError("Todo id must not be empty")
            if todo_id in ids:
                raise ToolError("Todo ids must be unique")
            if not content:
                raise ToolError("Todo content must not be empty")
            if status not in VALID_STATUSES:
                raise ToolError(f"Invalid todo status: {status}")
            ids.add(todo_id)
            normalized.append(
                TodoItem(
                    id=todo_id,
                    content=content,
                    status=cast(TodoStatus, status),
                    note=note,
                )
            )
        self._todos = normalized
        return {"ok": True, "count": len(normalized)}

    def update(self, todo_id: str, status: TodoStatus, note: str = "") -> dict:
        todo_id = todo_id.strip()
        if status not in VALID_STATUSES:
            raise ToolError(f"Invalid todo status: {status}")
        for todo in self._todos:
            if todo["id"] == todo_id:
                todo["status"] = status
                todo["note"] = note.strip()
                return {"ok": True, "todo": deepcopy(todo)}
        raise ToolError(f"Unknown todo: {todo_id}")

    def snapshot(self) -> list[TodoItem]:
        return deepcopy(self._todos)


def build_todo_tools(tracker: TodoTracker) -> list[StructuredTool]:
    def write(todos: list[dict]) -> dict:
        """Publish the complete execution Todo list, replacing the previous list.

        Each item needs a unique id, non-empty content, and one of pending, in_progress,
        completed, or blocked.
        """
        return tracker.write(todos)

    def update(todo_id: str, status: TodoStatus, note: str = "") -> dict:
        """Update one existing Todo item by id with its current status and optional evidence."""
        return tracker.update(todo_id, status, note)

    return [
        StructuredTool.from_function(write, name="TodoWriteTool"),
        StructuredTool.from_function(update, name="TodoUpdateTool"),
    ]


def build_todo_update_tool(tracker: TodoTracker) -> StructuredTool:
    """Build only the status-update capability used by the Actor."""

    def update(todo_id: str, status: TodoStatus, note: str = "") -> dict:
        """Update one existing Planner Todo by id with status and optional evidence."""
        return tracker.update(todo_id, status, note)

    return StructuredTool.from_function(update, name="TodoUpdateTool")
