"""Load only explicit configuration; constructing a model does not make a request."""

import os
from pathlib import Path
from urllib.parse import urlparse

from dotenv import dotenv_values
from langchain_openai import ChatOpenAI


def create_model(*, env_file: Path | None = None) -> ChatOpenAI:
    path = Path(env_file) if env_file is not None else Path.cwd() / ".env"
    if env_file is not None and not path.is_file():
        raise ValueError("The specified env file does not exist")
    values = dotenv_values(path, interpolate=False) if path.is_file() else {}

    def setting(name):
        return (os.environ.get(name, values.get(name)) or "").strip()

    api_key = setting("OPENAI_API_KEY")
    model_name = setting("OPENAI_MODEL")
    base_url = setting("OPENAI_BASE_URL")
    if not api_key:
        raise ValueError("Set OPENAI_API_KEY in the environment or .env")
    if not model_name:
        raise ValueError("Set OPENAI_MODEL to a model supporting tool calls")
    if base_url:
        parsed = urlparse(base_url)
        if (
            parsed.scheme not in {"https", "http"}
            or not parsed.hostname
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("OPENAI_BASE_URL must be an http(s) URL without credentials or query")
        if parsed.scheme == "http" and parsed.hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("Use HTTPS for non-local model endpoints")
    # Use Chat Completions for compatible providers, with bounded retries/timeout.
    return ChatOpenAI(
        model=model_name,
        api_key=api_key,
        base_url=base_url or "https://api.openai.com/v1",
        timeout=60,
        max_retries=1,
        use_responses_api=False,
    )
