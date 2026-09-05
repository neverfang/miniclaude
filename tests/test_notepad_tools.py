from miniclaude.core.state import RuntimeState
from miniclaude.tools.notepad_tools import build_notepad_tools
from miniclaude.tools.registry import execute_tool


def test_notepad_missing_read_and_append_round_trip(tmp_path):
    runtime = RuntimeState(tmp_path)
    tools = build_notepad_tools(runtime)

    assert execute_tool(tools, "NotepadReadTool", {}) == {
        "ok": True,
        "path": "NOTEPAD.md",
        "content": "",
        "exists": False,
        "truncated": False,
    }
    assert execute_tool(tools, "NotepadAppendTool", {"note": "first"})["ok"] is True
    assert execute_tool(tools, "NotepadAppendTool", {"note": "second\n"})["ok"] is True
    result = execute_tool(tools, "NotepadReadTool", {})
    assert result["content"] == "first\nsecond\n"
    assert (tmp_path / "NOTEPAD.md").read_text(encoding="utf-8") == result["content"]


def test_notepad_rejects_empty_nul_and_oversized_notes(tmp_path):
    tools = build_notepad_tools(RuntimeState(tmp_path), max_bytes=12)

    assert execute_tool(tools, "NotepadAppendTool", {"note": " "})["ok"] is False
    assert execute_tool(tools, "NotepadAppendTool", {"note": "bad\x00note"})["ok"] is False
    assert execute_tool(tools, "NotepadAppendTool", {"note": "x" * 20})["ok"] is False
    assert not (tmp_path / "NOTEPAD.md").exists()


def test_notepad_read_is_bounded_and_path_is_not_model_controlled(tmp_path):
    runtime = RuntimeState(tmp_path, max_output_chars=5)
    (tmp_path / "NOTEPAD.md").write_text("abcdefgh", encoding="utf-8")
    tools = build_notepad_tools(runtime)

    result = execute_tool(tools, "NotepadReadTool", {})

    assert result["content"] == "abcde"
    assert result["truncated"] is True
    assert {tool.name for tool in tools} == {"NotepadAppendTool", "NotepadReadTool"}
