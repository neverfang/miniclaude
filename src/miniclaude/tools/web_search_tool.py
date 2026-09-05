"""Bounded Tavily search exposed as one structured tool."""

import json
import os
from pathlib import Path
from typing import Protocol
from urllib.parse import urlparse

from dotenv import dotenv_values
from langchain_core.tools import StructuredTool

from miniclaude.core.state import ToolError


class SearchClient(Protocol):
    def search(self, query: str, **kwargs) -> dict: ...


def _api_key(env_file: Path | None) -> str:
    path = Path(env_file) if env_file is not None else Path.cwd() / ".env"
    values = dotenv_values(path, interpolate=False) if path.is_file() else {}
    return (os.environ.get("TAVILY_API_KEY", values.get("TAVILY_API_KEY")) or "").strip()


def _configured_client(env_file: Path | None) -> SearchClient:
    key = _api_key(env_file)
    if not key:
        raise ToolError("missing TAVILY_API_KEY")
    from tavily import TavilyClient

    return TavilyClient(api_key=key)


def _valid_url(value: object) -> bool:
    if not isinstance(value, str):
        return False
    parsed = urlparse(value)
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def _normalize_response(query: str, response: object, limit: int) -> dict:
    if not isinstance(response, dict) or not isinstance(response.get("results", []), list):
        return {"ok": False, "error": "malformed web search response"}
    empty_response = {"ok": True, "query": query, "answer": "", "results": []}
    baseline = len(json.dumps(empty_response, ensure_ascii=False))
    answer_budget = max(0, (limit - baseline) // 3)
    answer = str(response.get("answer") or "")[: min(2000, answer_budget)]
    results = []
    seen = set()
    for raw in response.get("results", []):
        if not isinstance(raw, dict) or not _valid_url(raw.get("url")):
            continue
        url = raw["url"].strip()
        key = url.casefold()
        if key in seen:
            continue
        seen.add(key)
        score = raw.get("score")
        results.append(
            {
                "title": str(raw.get("title") or url)[:300],
                "url": url,
                "content": str(raw.get("content") or "")[:1500],
                "score": float(score) if isinstance(score, int | float) else None,
            }
        )
        candidate = {"ok": True, "query": query, "answer": answer, "results": results}
        if len(json.dumps(candidate, ensure_ascii=False)) > limit:
            results.pop()
            break
    return {"ok": True, "query": query, "answer": answer, "results": results}


def build_web_search_tool(
    *,
    env_file: Path | None = None,
    client: SearchClient | None = None,
    max_output_chars: int = 12_000,
) -> StructuredTool:
    """Build a lazy, bounded Tavily search tool."""
    if max_output_chars < 500:
        raise ValueError("max_output_chars must be at least 500")

    def search(query: str, max_results: int = 5) -> dict:
        """Search the web for current facts and return bounded source records."""
        normalized_query = query.strip()
        if not normalized_query or len(normalized_query) > 500 or not 1 <= max_results <= 10:
            raise ToolError(
                "query must contain 1 to 500 characters and max_results must be between 1 and 10"
            )
        active_client = client or _configured_client(env_file)
        try:
            response = active_client.search(
                normalized_query,
                max_results=max_results,
                include_answer=True,
                include_raw_content=False,
            )
        except Exception as exc:
            return {"ok": False, "error": f"Web search failed ({type(exc).__name__})"}
        return _normalize_response(normalized_query, response, max_output_chars)

    return StructuredTool.from_function(search, name="WebSearchTool")
