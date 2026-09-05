"""Tool schemas and one error boundary between LLM arguments and Python functions."""

from langchain_core.tools import StructuredTool
from pydantic import ValidationError

from miniclaude.core.state import RuntimeState, ToolError
from miniclaude.tools.bash_tool import run_bash
from miniclaude.tools.file_tools import edit_file, read_file, write_file
from miniclaude.tools.grep_tool import grep


def _read_tool(state: RuntimeState) -> StructuredTool:
    def read(file_path: str, offset: int = 0, limit: int = 2000) -> dict:
        """Read UTF-8 text at a relative path with line numbers; offset is zero-based.

        Read before modifying an existing file. Max limit 2000 lines; outputs may be truncated.
        """
        return read_file(state, file_path, offset, limit)

    return StructuredTool.from_function(read, name="FileReadTool")


def _grep_tool(state: RuntimeState) -> StructuredTool:
    def search(
        pattern: str,
        path: str = ".",
        glob: str = "*",
        head_limit: int = 50,
        ignore_case: bool = False,
    ) -> dict:
        """Search UTF-8 text using regex, returning paths, line numbers and bounded matches.

        Search path is workspace-relative. Secrets, environment and Git folders are skipped.
        """
        return grep(state, pattern, path, glob, head_limit, ignore_case)

    return StructuredTool.from_function(search, name="GrepTool")


def build_readonly_tools(state: RuntimeState) -> list[StructuredTool]:
    """Build workspace-inspection tools that cannot modify files or run commands."""
    return [_read_tool(state), _grep_tool(state)]


def build_tools(state: RuntimeState) -> list[StructuredTool]:
    # Explicit signatures expose only model arguments, never RuntimeState.

    def write(file_path: str, content: str) -> dict:
        """Create a UTF-8 file. Before overwriting an existing file, read it with FileReadTool.

        Use workspace-relative paths. Existing files changed since reading will be rejected.
        """
        return write_file(state, file_path, content)

    def edit(file_path: str, old_text: str, new_text: str) -> dict:
        """Replace exactly one occurrence of old_text in a file previously read with FileReadTool.

        Preserve surrounding content. Read again after every modification; include unique context.
        """
        return edit_file(state, file_path, old_text, new_text)

    def bash(command: str, timeout_seconds: float | None = None) -> dict:
        """Run a noninteractive command in the workspace (PowerShell on Windows, sh on POSIX).

        Requires the user's --allow-shell opt-in. Use for tests and demos, not file editing.
        No background tasks. The working directory is NOT a sandbox. Max timeout 600 seconds.
        """
        return run_bash(state, command, timeout_seconds)

    return [
        _read_tool(state),
        StructuredTool.from_function(write, name="FileWriteTool"),
        StructuredTool.from_function(edit, name="FileEditTool"),
        _grep_tool(state),
        StructuredTool.from_function(bash, name="BashTool"),
    ]


def execute_tool(tools: list[StructuredTool], name: str, args: dict) -> dict:
    tool = next((tool for tool in tools if tool.name == name), None)
    if tool is None:
        return {"ok": False, "error": f"Unknown tool: {name}"}
    try:
        return tool.invoke(args)
    except ValidationError as exc:
        # Do not echo raw invalid input (it may contain sensitive or huge data).
        fields = ", ".join(".".join(map(str, e["loc"])) for e in exc.errors())
        return {"ok": False, "error": f"Invalid tool arguments: {fields}"}
    except ToolError as exc:
        return {"ok": False, "error": str(exc)}
    except (OSError, UnicodeError) as exc:
        return {"ok": False, "error": f"File or process operation failed ({type(exc).__name__})"}
