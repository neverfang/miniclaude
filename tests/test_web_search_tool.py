import json

from miniclaude.tools.registry import execute_tool
from miniclaude.tools.web_search_tool import _api_key, build_web_search_tool


class FakeClient:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    def search(self, query, **kwargs):
        self.calls.append((query, kwargs))
        if self.error:
            raise self.error
        return self.response


def test_tavily_key_environment_overrides_explicit_file(tmp_path, monkeypatch):
    env_file = tmp_path / "chosen.env"
    env_file.write_text("TAVILY_API_KEY=file-key\n", encoding="utf-8")
    monkeypatch.setenv("TAVILY_API_KEY", "environment-key")

    assert _api_key(env_file) == "environment-key"

    monkeypatch.delenv("TAVILY_API_KEY")
    assert _api_key(env_file) == "file-key"


def test_web_search_normalizes_deduplicates_and_bounds_results():
    client = FakeClient(
        {
            "answer": "summary",
            "results": [
                {
                    "title": "One",
                    "url": "https://example.com/a",
                    "content": "x" * 2000,
                    "score": 0.9,
                },
                {"title": "Duplicate", "url": "https://example.com/a", "content": "ignored"},
                {"title": "Unsafe", "url": "file:///secret", "content": "ignored"},
            ],
        }
    )
    tool = build_web_search_tool(client=client, max_output_chars=3000)

    result = execute_tool([tool], "WebSearchTool", {"query": " facts ", "max_results": 5})

    assert result["ok"] is True
    assert result["query"] == "facts"
    assert len(result["results"]) == 1
    assert len(result["results"][0]["content"]) == 1500
    assert client.calls[0][1] == {
        "max_results": 5,
        "include_answer": True,
        "include_raw_content": False,
    }


def test_web_search_missing_key_and_provider_errors_are_sanitized(tmp_path, monkeypatch):
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    missing = execute_tool(
        [build_web_search_tool(env_file=tmp_path / "missing.env")],
        "WebSearchTool",
        {"query": "facts"},
    )
    failing = execute_tool(
        [build_web_search_tool(client=FakeClient(error=RuntimeError("secret-token")))],
        "WebSearchTool",
        {"query": "facts"},
    )

    assert missing == {"ok": False, "error": "missing TAVILY_API_KEY"}
    assert failing["ok"] is False
    assert "RuntimeError" in failing["error"]
    assert "secret-token" not in failing["error"]


def test_web_search_rejects_invalid_arguments_and_malformed_response():
    tool = build_web_search_tool(client=FakeClient([]))
    assert execute_tool([tool], "WebSearchTool", {"query": " "})["ok"] is False
    assert execute_tool([tool], "WebSearchTool", {"query": "x", "max_results": 11})["ok"] is False
    malformed = execute_tool(
        [build_web_search_tool(client=FakeClient("bad"))], "WebSearchTool", {"query": "x"}
    )
    assert malformed["ok"] is False
    assert "malformed" in malformed["error"]


def test_web_search_bounds_answer_only_responses_and_query_length():
    tool = build_web_search_tool(
        client=FakeClient({"answer": "x" * 5000, "results": []}),
        max_output_chars=600,
    )

    result = execute_tool([tool], "WebSearchTool", {"query": "facts"})
    too_long = execute_tool([tool], "WebSearchTool", {"query": "q" * 501})

    assert len(json.dumps(result, ensure_ascii=False)) <= 600
    assert too_long["ok"] is False
