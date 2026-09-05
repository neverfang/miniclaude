import os

import pytest

from miniclaude.core.state import RuntimeState, ToolError
from miniclaude.tools.history_tools import (
    MAX_HISTORY_BYTES,
    persist_history_summary,
    read_history_summary,
)


def test_history_store_missing_then_replace_and_read(tmp_path):
    runtime = RuntimeState(tmp_path)

    assert read_history_summary(runtime) == {
        "ok": True,
        "path": "HISTORY_SUMMARY.md",
        "content": "",
        "exists": False,
        "truncated": False,
    }
    assert persist_history_summary(runtime, "first summary")["ok"] is True
    assert read_history_summary(runtime)["content"] == "first summary"
    persist_history_summary(runtime, "替换摘要")

    assert read_history_summary(runtime)["content"] == "替换摘要"
    assert (tmp_path / "HISTORY_SUMMARY.md").read_text(encoding="utf-8") == "替换摘要"


@pytest.mark.parametrize(
    ("summary", "error"),
    [
        (" ", "nonempty"),
        ("bad\x00summary", "NUL"),
        ("x" * (MAX_HISTORY_BYTES + 1), "65536"),
    ],
    ids=("blank", "nul", "oversized"),
)
def test_history_store_rejects_invalid_or_oversized_text(tmp_path, summary, error):
    runtime = RuntimeState(tmp_path)

    with pytest.raises(ToolError, match=error):
        persist_history_summary(runtime, summary)

    assert not (tmp_path / "HISTORY_SUMMARY.md").exists()


def test_history_store_returns_bounded_utf8_text(tmp_path):
    runtime = RuntimeState(tmp_path, max_output_chars=4)
    (tmp_path / "HISTORY_SUMMARY.md").write_text("历史摘要", encoding="utf-8")

    result = read_history_summary(runtime)

    assert result["content"] == "历史摘要"[:4]
    assert result["truncated"] is False


@pytest.mark.parametrize("data", [b"bad\x00text", b"\xff\xfe"])
def test_history_store_rejects_non_text_content_without_absolute_path(tmp_path, data):
    runtime = RuntimeState(tmp_path)
    (tmp_path / "HISTORY_SUMMARY.md").write_bytes(data)

    with pytest.raises(ToolError) as caught:
        read_history_summary(runtime)

    assert str(tmp_path) not in str(caught.value)


def test_history_store_rejects_hard_link(tmp_path):
    runtime = RuntimeState(tmp_path)
    outside_name = tmp_path / "other.md"
    outside_name.write_text("shared", encoding="utf-8")
    try:
        os.link(outside_name, tmp_path / "HISTORY_SUMMARY.md")
    except OSError:
        pytest.skip("Creating hard links requires OS permission")

    with pytest.raises(ToolError, match="regular non-linked"):
        read_history_summary(runtime)
