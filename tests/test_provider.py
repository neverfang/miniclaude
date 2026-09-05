import json

import httpx
import pytest
from langchain_openai import ChatOpenAI

from miniclaude.core.agent import stream_agent_events
from miniclaude.providers.openai_provider import create_model


@pytest.fixture(autouse=True)
def no_real_config(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    for key in (
        "OPENAI_API_KEY",
        "OPENAI_MODEL",
        "OPENAI_BASE_URL",
        "OPENAI_API_BASE",
        "OPENAI_THINKING",
    ):
        monkeypatch.delenv(key, raising=False)


def test_configuration_required_without_request():
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        create_model()


def test_model_name_is_required(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-only")
    with pytest.raises(ValueError, match="OPENAI_MODEL"):
        create_model()


def test_explicit_env_and_environment_precedence(tmp_path, monkeypatch):
    config = tmp_path / "config.env"
    config.write_text(
        "OPENAI_API_KEY=file-test\nOPENAI_MODEL=file-model\nOPENAI_BASE_URL=https://example.invalid/v1\n"
    )
    monkeypatch.setenv("OPENAI_MODEL", "env-model")
    model = create_model(env_file=config)
    assert model.model_name == "env-model"
    assert model.openai_api_base == "https://example.invalid/v1"
    assert model.openai_api_key.get_secret_value() == "file-test"
    assert model.request_timeout == 60
    assert model.max_retries == 1


def test_does_not_search_parent_dotenv(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("OPENAI_API_KEY=test\nOPENAI_MODEL=test\n")
    child = tmp_path / "child"
    child.mkdir()
    monkeypatch.chdir(child)
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        create_model()


def test_missing_explicit_env_file_is_reported(tmp_path):
    with pytest.raises(ValueError, match="env"):
        create_model(env_file=tmp_path / "absent.env")


def test_deepseek_official_endpoint_disables_thinking_for_tool_roundtrips(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-only")
    monkeypatch.setenv("OPENAI_MODEL", "deepseek-model")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://api.deepseek.com")

    model = create_model()

    assert model.extra_body == {"thinking": {"type": "disabled"}}


def test_compatible_gateway_can_explicitly_disable_thinking(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-only")
    monkeypatch.setenv("OPENAI_MODEL", "deepseek-model")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://gateway.example/v1")
    monkeypatch.setenv("OPENAI_THINKING", "disabled")

    model = create_model()

    assert model.extra_body == {"thinking": {"type": "disabled"}}


def test_invalid_thinking_setting_is_rejected(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "test-only")
    monkeypatch.setenv("OPENAI_MODEL", "test-model")
    monkeypatch.setenv("OPENAI_THINKING", "sometimes")

    with pytest.raises(ValueError, match="OPENAI_THINKING"):
        create_model()


def test_openai_compatible_wire_protocol_with_real_tool_execution(tmp_path):
    requests = []

    def respond(request):
        payload = json.loads(request.content)
        requests.append(payload)
        if len(requests) == 1:
            message = {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "wire-call",
                        "type": "function",
                        "function": {
                            "name": "FileWriteTool",
                            "arguments": json.dumps({"file_path": "wire.txt", "content": "你好"}),
                        },
                    }
                ],
            }
            finish_reason = "tool_calls"
        else:
            message = {"role": "assistant", "content": "Created wire.txt"}
            finish_reason = "stop"
        return httpx.Response(
            200,
            json={
                "id": "test-completion",
                "object": "chat.completion",
                "created": 0,
                "model": "test-model",
                "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 10, "total_tokens": 20},
            },
        )

    # Mock only the HTTP boundary; actual ChatOpenAI serialization and parsing run.
    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        model = ChatOpenAI(
            api_key="test-only",
            model="test-model",
            http_client=client,
            base_url="https://example.invalid/v1",
            use_responses_api=False,
        )
        events = list(stream_agent_events("Create wire.txt", workspace=tmp_path, model=model))
    assert events[-1]["type"] == "final_answer"
    assert (tmp_path / "wire.txt").read_text(encoding="utf-8") == "你好"
    assert len(requests[0]["tools"]) == 5
    observation = requests[1]["messages"][-1]
    assert observation["role"] == "tool"
    assert observation["tool_call_id"] == "wire-call"
    assert json.loads(observation["content"])["ok"]
