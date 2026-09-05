"""Research-only specialist with a single web search capability."""

import json
from collections.abc import Callable

from langchain_core.tools import StructuredTool

from miniclaude.core.agent import ChatModel, stream_agent_events
from miniclaude.graph.memory import prompt_memory_fields
from miniclaude.graph.state import MiniclaudeGraphState, SourceItem
from miniclaude.prompts.stage3 import SEARCH_AGENT_PROMPT


def run_search_agent(
    state: MiniclaudeGraphState,
    instruction: str,
    *,
    model: ChatModel,
    web_search_tool: StructuredTool,
    writer: Callable[[dict], None] | None = None,
    max_loops: int = 4,
) -> dict:
    """Run bounded research and return normalized evidence."""
    captured_messages = []
    tool_events = []
    queries = []
    sources: list[SourceItem] = []
    seen_urls = set()
    summary = ""
    error = ""
    task_data = {
        "user_task": state["task"],
        "instruction": instruction,
        "existing_research": state.get("research_notes", "")[:4000],
    }
    task_data.update(prompt_memory_fields(state))
    task = json.dumps(task_data, ensure_ascii=False)
    for event in stream_agent_events(
        task,
        workspace=state["runtime"].workspace,
        runtime=state["runtime"],
        model=model,
        tools=[web_search_tool],
        system_prompt=SEARCH_AGENT_PROMPT,
        max_loops=max_loops,
        captured_messages=captured_messages,
    ):
        if event["type"] not in {"run_start", "model_start"}:
            tool_events.append(event)
        if writer is not None:
            writer({"type": "search_agent_event", "event": event})
        if event["type"] == "tool_result":
            result = event["result"]
            if result.get("ok"):
                query = str(result.get("query", "")).strip()
                if query:
                    queries.append(query)
                for source in result.get("results", []):
                    url = source.get("url", "").casefold()
                    if url and url not in seen_urls:
                        seen_urls.add(url)
                        sources.append(source)
            else:
                error = result.get("error", "Web search failed")
        elif event["type"] == "final_answer":
            summary = event["content"]
        elif event["type"] == "error":
            error = event["message"]
    if summary and not error and not sources:
        error = "SearchAgent completed without a valid source"
    return {
        "ok": bool(summary) and not error,
        "summary": error or summary or "SearchAgent ended without a summary",
        "queries": queries,
        "sources": sources,
        "messages": captured_messages,
        "tool_events": tool_events,
    }
