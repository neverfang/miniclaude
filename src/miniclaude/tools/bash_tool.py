"""Opt-in local Shell execution. cwd and environment filtering are not a sandbox."""

import math
import os
import signal
import subprocess
import sys
import threading
from pathlib import Path

from miniclaude.core.approval import ApprovalDecision, classify_command_risk, make_approval_request
from miniclaude.core.state import RuntimeState, ToolError


def _child_environment() -> dict[str, str]:
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.upper().startswith(("OPENAI_", "ANTHROPIC_", "LANGSMITH_", "LANGCHAIN_"))
        and not key.upper().endswith(("_API_KEY", "_TOKEN", "_SECRET"))
    }
    env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env.get("PATH", "")
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    return env


def _stop_tree(process: subprocess.Popen):
    if os.name == "nt":
        # taskkill is best effort; no claim of a Windows Job Object sandbox.
        system_root = os.environ.get("SystemRoot", r"C:\Windows")
        try:
            subprocess.run(
                [
                    str(Path(system_root) / "System32/taskkill.exe"),
                    "/PID",
                    str(process.pid),
                    "/T",
                    "/F",
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=3,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        except (OSError, subprocess.TimeoutExpired):
            pass
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    if process.poll() is None:
        process.kill()




def _emit_runtime_event(state: RuntimeState, event: dict[str, object]) -> None:
    if state.event_handler is None:
        return
    try:
        state.event_handler(event)
    except Exception:
        pass


def _approval_result(state: RuntimeState, command: str) -> dict[str, object] | None:
    risk = classify_command_risk(command)
    if risk.level == "safe":
        return None

    request = make_approval_request(command, risk, state.workspace)
    base: dict[str, object] = {
        "requires_approval": risk.level == "risky",
        "approval_id": request.id,
        "risk_level": risk.level,
        "risk_reason": risk.reason,
        "approved": False,
    }
    if risk.level == "blocked":
        result = {**base, "ok": False, "error": f"blocked command: {risk.reason}"}
        _emit_runtime_event(state, {"type": "approval_resolved", **result})
        return result

    if state.approval_mode == "auto":
        result = {**base, "approved": True}
        _emit_runtime_event(state, {"type": "approval_resolved", **result})
        return result

    if state.approval_mode == "deny" or state.approval_handler is None:
        result = {**base, "ok": False, "error": f"approval denied: {risk.reason}"}
        _emit_runtime_event(state, {"type": "approval_resolved", **result})
        return result

    _emit_runtime_event(
        state,
        {"type": "approval_requested", **base, "command": command},
    )
    try:
        decision = state.approval_handler(request)
        approved = isinstance(decision, ApprovalDecision) and decision.approved
    except Exception:
        approved = False
    resolved = {**base, "approved": approved}
    _emit_runtime_event(state, {"type": "approval_resolved", **resolved})
    if not approved:
        return {
            **resolved,
            "ok": False,
            "error": f"approval denied: {risk.reason}",
        }
    return resolved
def run_bash(state: RuntimeState, command: str, timeout_seconds: float | None = None) -> dict:
    if not state.allow_shell:
        raise ToolError("Shell is disabled. The user must opt in with --allow-shell")
    timeout = state.command_timeout if timeout_seconds is None else timeout_seconds
    if not math.isfinite(timeout) or not 0 < timeout <= 600:
        raise ToolError("timeout_seconds must be finite, greater than 0 and at most 600")
    if not command.strip() or len(command) > 16_000 or "\x00" in command:
        raise ToolError("Provide a nonempty command of at most 16000 characters")
    approval = _approval_result(state, command)
    if approval is not None and not approval.get("approved"):
        return approval
    if os.name == "nt":
        shell = (
            Path(os.environ.get("SystemRoot", r"C:\Windows"))
            / "System32/WindowsPowerShell/v1.0/powershell.exe"
        )
        # PowerShell otherwise maps every native nonzero exit status to 1.
        wrapped = (
            (
                "[Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)\n"
                "$OutputEncoding = [Console]::OutputEncoding\n"
            )
            + command
            + (
                "\n$miniclaudeCommandOk = $?\n"
                "if ($miniclaudeCommandOk) { exit 0 }\n"
                "if ($LASTEXITCODE) { exit $LASTEXITCODE }\n"
                "exit 1\n"
            )
        )
        argv = [str(shell), "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", wrapped]
        platform_options = {"creationflags": subprocess.CREATE_NO_WINDOW}
    else:
        argv = ["/bin/sh", "-c", command]
        platform_options = {"start_new_session": True}

    outputs = [bytearray(), bytearray()]
    overflows = [False, False]
    byte_limit = state.max_output_chars * 4

    def drain(pipe, index):
        try:
            while chunk := pipe.read(4096):
                remaining = byte_limit - len(outputs[index])
                outputs[index].extend(chunk[: max(0, remaining)])
                overflows[index] |= len(chunk) > remaining
        finally:
            pipe.close()

    process = subprocess.Popen(
        argv,
        cwd=state.workspace,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=_child_environment(),
        **platform_options,
    )
    readers = [
        threading.Thread(target=drain, args=(pipe, index), daemon=True)
        for index, pipe in enumerate((process.stdout, process.stderr))
    ]
    for reader in readers:
        reader.start()
    timed_out = False
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        _stop_tree(process)
        process.wait(timeout=5)
    except BaseException:
        _stop_tree(process)
        process.wait(timeout=5)
        raise
    finally:
        for reader in readers:
            reader.join(timeout=1)
    incomplete = any(reader.is_alive() for reader in readers)
    decoded = [data.decode("utf-8", errors="replace") for data in outputs]
    result = {
        "ok": process.returncode == 0 and not timed_out and not incomplete,
        "exit_code": process.returncode,
        "stdout": decoded[0][: state.max_output_chars],
        "stderr": decoded[1][: state.max_output_chars],
        "timed_out": timed_out,
        "truncated": any(overflows) or any(len(s) > state.max_output_chars for s in decoded),
    }
    result.update(approval or {})
    if timed_out:
        result["error"] = "Command timed out; process-tree termination was attempted"
    elif incomplete:
        result["error"] = (
            "Background process still holds output pipes; background tasks are unsupported"
        )
    elif process.returncode:
        result["error"] = f"Command exited with code {process.returncode}"
    return result
