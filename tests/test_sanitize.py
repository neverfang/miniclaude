import json
import math
from pathlib import Path

from miniclaude.core.sanitize import MAX_PERSISTED_STRING, sanitize_for_persistence


def test_sanitize_redacts_fields_and_inline_secrets():
    payload = {
        "api_key": "sk-secret-value",
        "nested": {"Authorization": "Bearer abc.def.ghi"},
        "command": "curl -H 'Authorization: Bearer top-secret' https://example.test",
        "password_hint": "never-store-this",
        "safe": "pytest -q",
    }

    clean = sanitize_for_persistence(payload)
    rendered = json.dumps(clean)

    assert "secret-value" not in rendered
    assert "abc.def.ghi" not in rendered
    assert "top-secret" not in rendered
    assert "never-store-this" not in rendered
    assert clean["api_key"] == "[REDACTED]"
    assert clean["nested"]["Authorization"] == "[REDACTED]"
    assert clean["safe"] == "pytest -q"


def test_sanitize_redacts_url_credentials_and_assignment_syntax():
    clean = sanitize_for_persistence(
        {
            "url": "https://alice:hunter2@example.test/data",
            "command": "tool --token=token-value --password hunter2",
        }
    )
    rendered = json.dumps(clean)

    assert "alice" not in rendered
    assert "hunter2" not in rendered
    assert "token-value" not in rendered
    assert "[REDACTED]" in rendered


def test_sanitize_bounds_unknown_objects_cycles_and_collections():
    cycle = []
    cycle.append(cycle)

    clean = sanitize_for_persistence(
        {
            "long": "x" * 20_000,
            "cycle": cycle,
            "unknown": object(),
            "many": list(range(150)),
        }
    )

    assert len(clean["long"]) <= MAX_PERSISTED_STRING
    assert clean["cycle"][0] == "[CYCLE]"
    assert clean["unknown"] == "[object]"
    assert clean["many"][-1] == "[TRUNCATED_ITEMS]"


def test_sanitize_keeps_safe_scalars_and_hides_absolute_paths():
    clean = sanitize_for_persistence(
        {
            "none": None,
            "boolean": True,
            "integer": 7,
            "float": 2.5,
            "not_finite": math.inf,
            "relative": Path("src/app.py"),
            "absolute": Path("C:/Users/private/file.txt"),
        }
    )

    assert clean["none"] is None
    assert clean["boolean"] is True
    assert clean["integer"] == 7
    assert clean["float"] == 2.5
    assert clean["not_finite"] == "[NON_FINITE_NUMBER]"
    assert clean["relative"] == "src/app.py"
    assert clean["absolute"] == "[ABSOLUTE_PATH]"


def test_sanitize_keeps_bounded_internal_token_metrics():
    clean = sanitize_for_persistence(
        {
            "context_token_count": 12_345,
            "context_token_limit": 400_000,
            "access_token": "secret-value",
        }
    )

    assert clean == {
        "context_token_count": 12_345,
        "context_token_limit": 400_000,
        "access_token": "[REDACTED]",
    }
