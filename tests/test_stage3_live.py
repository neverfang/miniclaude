"""Explicit opt-in end-to-end acceptance for stage three."""

import os
import re
import subprocess
import sys

import pytest

pytestmark = pytest.mark.live


def test_live_stage3_researched_html(request, tmp_path):
    if not request.config.getoption("--run-live-stage3"):
        pytest.skip("Requires --run-live-stage3: paid model/search calls and generated code")
    required = ["OPENAI_API_KEY", "OPENAI_MODEL", "TAVILY_API_KEY"]
    missing = [name for name in required if not os.getenv(name)]
    if missing:
        pytest.skip("Live environment is missing: " + ", ".join(missing))

    task = (
        "Research the official Python documentation for dataclasses and pathlib. Create "
        "research.html as a finite standalone UTF-8 page summarizing when to use each, "
        "with at least two visible HTTP(S) source links. Do not start a server or GUI. "
        "Verify the HTML file and links."
    )
    command = [
        sys.executable,
        "-m",
        "miniclaude",
        task,
        "--workspace",
        str(tmp_path),
        "--allow-shell",
        "--max-loops",
        "20",
        "--max-attempts",
        "3",
    ]
    completed = subprocess.run(
        command,
        cwd=request.config.rootpath,
        capture_output=True,
        text=True,
        timeout=600,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    output = tmp_path / "research.html"
    assert output.is_file()
    html = output.read_text(encoding="utf-8")
    assert len(set(re.findall(r"https?://[^\"'<>\s]+", html))) >= 2
