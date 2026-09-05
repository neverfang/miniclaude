from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import StructuredTool

from miniclaude.agents.code_agent import run_code_agent
from miniclaude.agents.search_agent import run_search_agent
from miniclaude.core.state import RuntimeState
from miniclaude.graph.state import TodoItem, initial_graph_state


class SequenceModel:
    def __init__(self, replies):
        self.replies = iter(replies)
        self.tool_names = []
        self.inputs = []

    def bind_tools(self, tools):
        self.tool_names = [tool.name for tool in tools]
        return self

    def invoke(self, messages):
        self.inputs.append(list(messages))
        return next(self.replies)


def test_search_agent_has_only_web_search_and_collects_sources(tmp_path):
    def search(query: str, max_results: int = 5) -> dict:
        """Return deterministic research."""
        return {
            "ok": True,
            "query": query,
            "answer": "answer",
            "results": [
                {
                    "title": "Official",
                    "url": "https://example.com/docs",
                    "content": "facts",
                    "score": 1.0,
                }
            ],
        }

    model = SequenceModel(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "WebSearchTool", "args": {"query": "facts"}, "id": "s1"},
                    {"name": "WebSearchTool", "args": {"query": "more facts"}, "id": "s2"},
                ],
            ),
            AIMessage(content="Research complete"),
        ]
    )
    state = initial_graph_state("research", runtime=RuntimeState(tmp_path))
    state["context_summary"] = "compressed research history"
    state["memory_snapshot"] = {
        "rules": {},
        "working_memory": {"research_notes": "prior evidence"},
        "history_summary_store": {},
    }

    result = run_search_agent(
        state,
        "find facts",
        model=model,
        web_search_tool=StructuredTool.from_function(search, name="WebSearchTool"),
    )

    assert model.tool_names == ["WebSearchTool"]
    assert result["ok"] is True
    assert result["summary"] == "Research complete"
    assert result["queries"] == ["facts", "more facts"]
    assert result["sources"][0]["url"] == "https://example.com/docs"
    assert len(result["messages"]) == 6
    human = next(message for message in model.inputs[0] if isinstance(message, HumanMessage))
    assert "context_summary_untrusted" in human.content
    assert "compressed research history" in human.content
    assert "memory_snapshot_untrusted" in human.content
    assert "prior evidence" in human.content


def test_code_agent_has_implementation_tools_and_updates_todo_and_notepad(tmp_path):
    model = SequenceModel(
        [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "TodoUpdateTool",
                        "args": {"todo_id": "code", "status": "completed", "note": "done"},
                        "id": "t1",
                    },
                    {
                        "name": "NotepadAppendTool",
                        "args": {"note": "implemented feature"},
                        "id": "n1",
                    },
                ],
            ),
            AIMessage(content="Implementation complete"),
        ]
    )
    state = initial_graph_state("build", runtime=RuntimeState(tmp_path, allow_shell=True))
    state["todos"] = [TodoItem(id="code", content="Implement", status="in_progress", note="")]
    state["research_notes"] = "official facts"
    state["sources"] = [
        {"title": "Docs", "url": "https://example.com", "content": "facts", "score": 1.0}
    ]
    state["context_summary"] = "compressed implementation history"
    state["memory_snapshot"] = {
        "rules": {},
        "working_memory": {"important_files": ["app.py"]},
        "history_summary_store": {"notepad": "keep tests green"},
    }

    result = run_code_agent(state, "create output", model=model)

    assert "WebSearchTool" not in model.tool_names
    assert {"FileReadTool", "FileWriteTool", "FileEditTool", "GrepTool", "BashTool"}.issubset(
        model.tool_names
    )
    assert {"TodoUpdateTool", "NotepadAppendTool", "NotepadReadTool"}.issubset(model.tool_names)
    assert result["ok"] is True
    assert result["todos"][0]["status"] == "completed"
    assert (tmp_path / "NOTEPAD.md").read_text(encoding="utf-8") == "implemented feature\n"
    human = next(message for message in model.inputs[0] if isinstance(message, HumanMessage))
    assert "official facts" in human.content
    assert "https://example.com" in human.content
    assert "context_summary_untrusted" in human.content
    assert "compressed implementation history" in human.content
    assert "memory_snapshot_untrusted" in human.content
    assert "app.py" in human.content


def test_specialist_prompt_omits_empty_or_absent_stage_four_memory(tmp_path):
    model = SequenceModel([AIMessage(content="No research needed")])
    state = initial_graph_state("research", runtime=RuntimeState(tmp_path))
    state.pop("context_summary")
    state.pop("memory_snapshot")
    web_tool = StructuredTool.from_function(
        lambda query: {"ok": True, "query": query, "results": []},
        name="WebSearchTool",
        description="Return deterministic research.",
    )

    run_search_agent(state, "inspect", model=model, web_search_tool=web_tool)

    human = next(message for message in model.inputs[0] if isinstance(message, HumanMessage))
    assert "context_summary_untrusted" not in human.content
    assert "memory_snapshot_untrusted" not in human.content


def test_specialists_report_empty_or_exhausted_model_runs(tmp_path):
    state = initial_graph_state("task", runtime=RuntimeState(tmp_path))
    web_tool = StructuredTool.from_function(
        lambda query: {"ok": True},
        name="WebSearchTool",
        description="Return deterministic research.",
    )

    empty = run_search_agent(
        state, "research", model=SequenceModel([AIMessage(content="")]), web_search_tool=web_tool
    )
    exhausted = run_code_agent(
        state,
        "implement",
        model=SequenceModel(
            [AIMessage(content="", tool_calls=[{"name": "Unknown", "args": {}, "id": "x"}])]
        ),
        max_loops=1,
    )

    assert empty["ok"] is False
    assert exhausted["ok"] is False


def test_code_agent_requires_an_explicit_successful_todo_update(tmp_path):
    state = initial_graph_state("build", runtime=RuntimeState(tmp_path))
    state["todos"] = [TodoItem(id="code", content="Implement", status="in_progress", note="")]

    result = run_code_agent(
        state,
        "implement",
        model=SequenceModel([AIMessage(content="Implementation complete")]),
    )

    assert result["ok"] is False
    assert "TodoUpdateTool" in result["summary"]


def test_search_agent_requires_at_least_one_valid_source(tmp_path):
    def empty_search(query: str) -> dict:
        """Return a successful search with no usable sources."""
        return {"ok": True, "query": query, "answer": "none", "results": []}

    model = SequenceModel(
        [
            AIMessage(
                content="",
                tool_calls=[{"name": "WebSearchTool", "args": {"query": "facts"}, "id": "s1"}],
            ),
            AIMessage(content="No sources found"),
        ]
    )
    state = initial_graph_state("research", runtime=RuntimeState(tmp_path))

    result = run_search_agent(
        state,
        "find facts",
        model=model,
        web_search_tool=StructuredTool.from_function(empty_search, name="WebSearchTool"),
    )

    assert result["ok"] is False
    assert "source" in result["summary"].casefold()
