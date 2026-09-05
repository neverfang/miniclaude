import pytest
from langchain_core.messages import HumanMessage

from miniclaude.core.state import RuntimeState
from miniclaude.graph.context import (
    context_monitor_route,
    estimate_context_tokens,
    make_context_monitor_node,
)
from miniclaude.graph.state import initial_graph_state


class CountingModel:
    def __init__(self, count: int) -> None:
        self.count = count
        self.messages = []

    def get_num_tokens_from_messages(self, messages):
        self.messages = list(messages)
        return self.count


@pytest.mark.parametrize(
    ("count", "should_compress", "route"),
    [(99, False, "verifier"), (100, True, "compressor")],
)
def test_context_monitor_uses_exact_threshold(tmp_path, count, should_compress, route):
    state = initial_graph_state("task", runtime=RuntimeState(tmp_path))
    state["messages"] = [HumanMessage(content="context")]
    state["context_token_limit"] = 100
    events = []

    update = make_context_monitor_node(CountingModel(count), emit=events.append)(state)

    assert update["context_token_count"] == count
    assert update["context_should_compress"] is should_compress
    assert update["context_next_node"] == route
    assert update["context_count_method"] == "model"
    assert context_monitor_route({**state, **update}) == route
    assert "memory_snapshot" not in update
    assert events == [
        {
            "type": "context_monitor",
            "tokens": count,
            "limit": 100,
            "method": "model",
            "route": route,
        }
    ]


def test_token_estimator_sanitizes_before_using_model_counter():
    counter = CountingModel(7)

    count, method = estimate_context_tokens(
        [HumanMessage(content="DEEPSEEK_API_KEY=secret-value-123")],
        counter,
    )

    assert (count, method) == (7, "model")
    assert "secret-value-123" not in str(counter.messages)
    assert "[REDACTED]" in str(counter.messages)


def test_token_estimator_falls_back_for_structured_content():
    class BrokenCounter:
        def get_num_tokens_from_messages(self, messages):
            raise RuntimeError("Bearer private-token-value")

    message = HumanMessage(content=[{"type": "text", "text": "hello"}])

    count, method = estimate_context_tokens([message], BrokenCounter())

    assert count >= 1
    assert method == "fallback"


def test_context_monitor_counts_sanitized_layered_memory_payload(tmp_path):
    state = initial_graph_state("task", runtime=RuntimeState(tmp_path))
    state["messages"] = [HumanMessage(content="message")]
    state["memory_snapshot"] = {
        "rules": {},
        "working_memory": {"note": "Bearer private-memory-secret"},
        "history_summary_store": {},
    }
    counter = CountingModel(12)
    events = []

    make_context_monitor_node(counter, emit=events.append)(state)

    assert len(counter.messages) == 2
    assert "private-memory-secret" not in str(counter.messages)
    assert "[REDACTED]" in str(counter.messages[-1])
    assert "memory" not in events[0]


def test_context_monitor_preserves_final_route(tmp_path):
    state = initial_graph_state("task", runtime=RuntimeState(tmp_path))
    state["context_token_limit"] = 1
    state["context_next_node"] = "final"
    state["passed"] = True

    update = make_context_monitor_node(CountingModel(10))(state)

    assert update["context_should_compress"] is False
    assert update["context_next_node"] == "final"


def test_context_monitor_fails_closed_with_sanitized_error(tmp_path):
    class ExplodingMessages:
        def __iter__(self):
            raise RuntimeError("Bearer private-token-value")

    state = initial_graph_state("task", runtime=RuntimeState(tmp_path))
    state["messages"] = ExplodingMessages()

    update = make_context_monitor_node(CountingModel(1))(state)

    assert update["context_next_node"] == "final"
    assert update["context_should_compress"] is False
    assert "private-token-value" not in update["context_error"]
    assert "RuntimeError" in update["context_error"]
