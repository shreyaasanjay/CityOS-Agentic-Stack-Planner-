"""Local web UI that runs the real TraceFix LLM pipeline.

The server starts TraceFix commands as subprocesses, injects API keys through
the child environment, and streams stdout/stderr to the browser with SSE.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import queue
import re
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse


STATIC_DIR = Path(__file__).resolve().parent / "static"
RUNS: dict[str, "RunState"] = {}
RUNS_LOCK = threading.Lock()


def _repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "pyproject.toml").exists():
            return parent
    return Path.cwd()


def _read_text(path: Path, limit: int = 180_000) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    if len(text) > limit:
        return text[:limit] + "\n\n... truncated in UI ..."
    return text


def _read_json(path: Path) -> Any | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _task_title(task_dir: Path) -> str:
    desc = _read_text(task_dir / "description.md", limit=3000)
    first_line = next((line.strip() for line in desc.splitlines() if line.strip()), "")
    match = re.match(r"^#\s*(.+)$", first_line)
    return match.group(1) if match else task_dir.name


def _task_options(root: Path) -> list[dict[str, str]]:
    desc_root = root / "benchmark" / "descriptions"
    if not desc_root.exists():
        return []

    def key(path: Path) -> tuple[int, int]:
        match = re.match(r"^(\d+)([EMH])$", path.name)
        if not match:
            return (999, 999)
        diff_order = {"E": 0, "M": 1, "H": 2}
        return (int(match.group(1)), diff_order[match.group(2)])

    return [
        {"id": task_dir.name, "title": _task_title(task_dir)}
        for task_dir in sorted(desc_root.iterdir(), key=key)
        if task_dir.is_dir()
    ]


def _redact_command(command: list[str]) -> list[str]:
    redacted = []
    skip_next = False
    for item in command:
        if skip_next:
            redacted.append("***")
            skip_next = False
            continue
        redacted.append(item)
        if item == "--api-key":
            skip_next = True
    return redacted


def _event_kind(line: str) -> str:
    clean = line.strip()
    if re.match(r"--- Turn \d+/\d+ ---", clean):
        return "turn"
    if clean.startswith("Tool: "):
        return "tool"
    if clean.startswith("Result: "):
        return "tool_result"
    if "PASS" in clean and len(clean) < 120:
        return "pass"
    if clean.startswith("FAIL") or "ERROR" in clean:
        return "error"
    if clean.startswith("Experiment dir:"):
        return "experiment"
    if clean.startswith("Workspace:") or clean.startswith("Session saved to:"):
        return "workspace"
    return "log"


def _parse_workspace_from_line(root: Path, line: str) -> Path | None:
    match = re.search(r"\bworkspace:\s*(.+)$", line, re.IGNORECASE)
    if not match:
        match = re.search(r"Workspace:\s*(.+)$", line)
    if not match:
        match = re.search(r"Session saved to:\s*(.+session\.json)$", line)
        if match:
            candidate = Path(match.group(1).strip()).parent
            return (root / candidate).resolve() if not candidate.is_absolute() else candidate
        match = re.search(r"run snapshot:\s*(.+?)(?:\s{2,}|\s+\(|$)", line, re.IGNORECASE)
        if not match:
            match = re.search(r"run snapshot\s*(?:->|â†’|→)\s*(.+)$", line, re.IGNORECASE)
        if not match:
            return None
    candidate = Path(match.group(1).strip().strip("`"))
    return (root / candidate).resolve() if not candidate.is_absolute() else candidate


def _parse_experiment_dir(root: Path, line: str) -> Path | None:
    match = re.search(r"Experiment dir:\s*(.+)$", line)
    if not match:
        return None
    candidate = Path(match.group(1).strip())
    return (root / candidate).resolve() if not candidate.is_absolute() else candidate


def _find_workspace(experiment_dir: Path | None) -> Path | None:
    if experiment_dir is None:
        return None
    workspace_root = experiment_dir / "workspaces"
    if not workspace_root.exists():
        return None
    candidates = [p for p in workspace_root.iterdir() if p.is_dir()]
    if not candidates:
        return None
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return candidates[0]


def _spec_file(workspace: Path, name: str) -> Path:
    spec = workspace / "spec"
    return (spec / name) if spec.is_dir() else (workspace / name)


def _prompt_files(workspace: Path) -> list[str]:
    prompt_root = workspace / "prompts"
    if not prompt_root.exists():
        return []
    return [
        str(path.relative_to(prompt_root)).replace("\\", "/")
        for path in sorted(prompt_root.rglob("*.md"))
    ]


def _artifact_snapshot(workspace: Path | None) -> dict[str, Any]:
    if workspace is None or not workspace.exists():
        return {
            "workspace": "",
            "files": [],
            "ir": None,
            "protocol": "",
            "states": None,
            "summary": None,
            "session": None,
            "tlcError": "",
            "prompts": [],
            "runResult": None,
        }

    files = []
    for path in sorted(workspace.rglob("*")):
        if path.is_file():
            files.append(str(path.relative_to(workspace)).replace("\\", "/"))

    return {
        "workspace": str(workspace),
        "files": files[:200],
        "ir": _read_json(_spec_file(workspace, "ir.json")),
        "protocol": _read_text(_spec_file(workspace, "Protocol.tla")),
        "states": _read_json(_spec_file(workspace, "states.json")),
        "summary": _read_json(_spec_file(workspace, "summary.json")),
        "session": _read_json(workspace / "session.json"),
        "tlcError": _read_text(_spec_file(workspace, "tlc_error.md")),
        "prompts": _prompt_files(workspace),
        "runResult": _read_json(workspace / "run_result.json"),
    }


@dataclass
class RunState:
    id: str
    root: Path
    command: list[str]
    env_keys: dict[str, bool]
    mode: str
    provider: str
    model: str
    status: str = "starting"
    started_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    ended_at: str = ""
    exit_code: int | None = None
    events: list[dict[str, Any]] = field(default_factory=list)
    subscribers: list[queue.Queue] = field(default_factory=list)
    process: subprocess.Popen | None = None
    experiment_dir: Path | None = None
    workspace: Path | None = None
    error: str = ""
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def publish(self, event: dict[str, Any]) -> None:
        event.setdefault("ts", time.time())
        with self._lock:
            self.events.append(event)
            subscribers = list(self.subscribers)
        for subscriber in subscribers:
            subscriber.put(event)

    def subscribe(self) -> queue.Queue:
        q: queue.Queue = queue.Queue()
        with self._lock:
            for event in self.events:
                q.put(event)
            self.subscribers.append(q)
        return q

    def unsubscribe(self, q: queue.Queue) -> None:
        with self._lock:
            if q in self.subscribers:
                self.subscribers.remove(q)

    def snapshot(self) -> dict[str, Any]:
        if self.workspace is None:
            self.workspace = _find_workspace(self.experiment_dir)
        return {
            "id": self.id,
            "mode": self.mode,
            "provider": self.provider,
            "model": self.model,
            "status": self.status,
            "startedAt": self.started_at,
            "endedAt": self.ended_at,
            "exitCode": self.exit_code,
            "command": _redact_command(self.command),
            "envKeys": self.env_keys,
            "experimentDir": str(self.experiment_dir or ""),
            "artifacts": _artifact_snapshot(self.workspace),
            "error": self.error,
        }


def _env_updates_for_provider(payload: dict[str, Any], *, require_key: bool) -> dict[str, str]:
    provider = str(payload.get("provider", "openai"))
    env_updates: dict[str, str] = {}
    key_map = {
        "openai": ("OPENAI_API_KEY", "openaiKey"),
        "anthropic": ("ANTHROPIC_API_KEY", "anthropicKey"),
        "openrouter": ("OPENROUTER_API_KEY", "openrouterKey"),
    }
    if provider not in key_map:
        return env_updates
    env_name, payload_name = key_map[provider]
    api_key = str(payload.get(payload_name, "")).strip()
    if api_key:
        env_updates[env_name] = api_key
    elif require_key:
        raise ValueError(f"{env_name} is required for provider {provider}")
    return env_updates


def _model_for_tracefix(payload: dict[str, Any]) -> str:
    provider = str(payload.get("provider", "openai"))
    model = str(payload.get("model", "")).strip()
    if not model:
        return ""
    if provider == "openrouter":
        return model if model.startswith("openrouter/") else f"openrouter/{model}"
    if "/" in model or provider == "ollama":
        return model
    return f"{provider}/{model}"


def _benchmark_task_text(root: Path, task_id: str) -> str:
    task_dir = root / "benchmark" / "descriptions" / task_id
    desc = _read_text(task_dir / "description.md")
    tools = _read_text(task_dir / "tools.json")
    metadata = _read_text(task_dir / "metadata.json")
    parts = [f"Benchmark task {task_id}", desc]
    if metadata:
        parts.append(f"metadata.json:\n{metadata}")
    if tools:
        parts.append(f"tools.json:\n{tools}")
    return "\n\n".join(part for part in parts if part.strip())


def _task_text_from_payload(root: Path, payload: dict[str, Any]) -> str:
    task_mode = str(payload.get("taskMode", "benchmark"))
    if task_mode == "custom":
        custom_task = str(payload.get("customTask", "")).strip()
        if not custom_task:
            raise ValueError("Custom task text is required")
        return custom_task
    task_id = str(payload.get("taskId", "3E")).strip() or "3E"
    return _benchmark_task_text(root, task_id)


def _build_pipeline_command(payload: dict[str, Any]) -> tuple[list[str], dict[str, str], dict[str, bool]]:
    provider = str(payload.get("provider", "openai"))
    model = str(payload.get("model", "gpt-5-mini")).strip() or "gpt-5-mini"
    task_mode = str(payload.get("taskMode", "benchmark"))
    task_id = str(payload.get("taskId", "3E")).strip() or "3E"
    custom_task = str(payload.get("customTask", "")).strip()
    max_turns = int(payload.get("maxTurns") or 20)
    max_tokens = int(payload.get("maxTokens") or 32768)
    temperature = float(payload.get("temperature") if payload.get("temperature") is not None else 0.3)
    ollama_url = str(payload.get("ollamaUrl", "http://localhost:11434/v1")).strip()

    command = [
        sys.executable,
        "-u",
        "-B",
        "-m",
        "tracefix.pipeline",
        "--provider",
        provider,
        "--model",
        model,
        "--max-turns",
        str(max_turns),
        "--max-tokens",
        str(max_tokens),
        "--temperature",
        str(temperature),
        "--verbose",
    ]
    if payload.get("noSummarize", True):
        command.append("--no-summarize")
    if payload.get("batchLint", False):
        command.append("--batch-lint")

    if provider == "ollama":
        command.extend(["--ollama-url", ollama_url])

    if task_mode == "custom":
        if not custom_task:
            raise ValueError("Custom task text is required")
        command.extend(["--task", custom_task])
    else:
        command.extend(["--benchmark", task_id])

    env_updates = _env_updates_for_provider(payload, require_key=True)
    env_flags = {name: bool(value) for name, value in env_updates.items()}
    return command, env_updates, env_flags


def _build_design_command(root: Path, payload: dict[str, Any]) -> tuple[list[str], dict[str, str], dict[str, bool]]:
    task_text = _task_text_from_payload(root, payload)
    timeout = float(payload.get("timeout") or 1800)
    opencode_bin = str(payload.get("opencodeBin", "opencode")).strip() or "opencode"
    command = [
        sys.executable,
        "-u",
        "-B",
        "-m",
        "tracefix.runtime.cli",
        "design",
        task_text,
        "--timeout",
        str(timeout),
        "--opencode-bin",
        opencode_bin,
    ]
    model = _model_for_tracefix(payload)
    if model:
        command.extend(["--model", model])
    if payload.get("live", False):
        command.append("--live")
    if payload.get("verbose", True):
        command.append("--verbose")
    env_updates = _env_updates_for_provider(payload, require_key=False)
    env_flags = {name: bool(value) for name, value in env_updates.items()}
    return command, env_updates, env_flags


def _build_runtime_command(payload: dict[str, Any]) -> tuple[list[str], dict[str, str], dict[str, bool]]:
    workspace = str(payload.get("workspacePath", "")).strip()
    if not workspace:
        raise ValueError("Workspace path is required")
    harness = str(payload.get("harness", "opencode")).strip() or "opencode"
    command = [
        sys.executable,
        "-u",
        "-B",
        "-m",
        "tracefix.runtime.cli",
        "run",
        "--workspace",
        workspace,
        "--harness",
        harness,
    ]
    raw_model = str(payload.get("model", "")).strip()
    model = _model_for_tracefix(payload) if harness == "opencode" else raw_model
    if model:
        command.extend(["--model", model])
    task = str(payload.get("runtimeTask", "")).strip()
    if task:
        command.extend(["--task", task])
    if payload.get("live", False):
        command.append("--live")
    if payload.get("verbose", True):
        command.append("--verbose")
    timeout = payload.get("timeout")
    if timeout:
        command.extend(["--timeout", str(timeout)])
    if harness == "opencode":
        opencode_bin = str(payload.get("opencodeBin", "opencode")).strip() or "opencode"
        command.extend(["--opencode-bin", opencode_bin])
    env_updates = _env_updates_for_provider(payload, require_key=False)
    env_flags = {name: bool(value) for name, value in env_updates.items()}
    return command, env_updates, env_flags


def _build_design_run_command(root: Path, payload: dict[str, Any]) -> tuple[list[str], dict[str, str], dict[str, bool]]:
    return _build_design_command(root, payload)


def _missing_provider_packages(provider: str) -> list[str]:
    import importlib.util

    if provider in {"openai", "openrouter", "ollama"}:
        if importlib.util.find_spec("openai") is None:
            return ["openai"]
    if provider == "anthropic":
        if importlib.util.find_spec("anthropic") is None:
            return ["anthropic"]
    return []


def _start_run(root: Path, payload: dict[str, Any]) -> RunState:
    mode = str(payload.get("mode", "pipeline"))
    provider = str(payload.get("provider", "openai"))

    if mode == "pipeline":
        missing = _missing_provider_packages(provider)
        if missing:
            packages = " ".join(missing)
            raise ValueError(
                f"Missing Python package(s): {packages}. "
                f"Install with: python -m pip install {packages}"
            )
        command, env_updates, env_flags = _build_pipeline_command(payload)
    elif mode == "design":
        command, env_updates, env_flags = _build_design_command(root, payload)
    elif mode == "runtime":
        command, env_updates, env_flags = _build_runtime_command(payload)
    elif mode == "design_run":
        command, env_updates, env_flags = _build_design_run_command(root, payload)
    else:
        raise ValueError(f"Unknown mode: {mode}")

    run_id = uuid.uuid4().hex[:10]
    run = RunState(
        id=run_id,
        root=root,
        command=command,
        env_keys=env_flags,
        mode=mode,
        provider=provider,
        model=str(payload.get("model", "")),
    )
    if mode == "runtime":
        workspace = Path(str(payload.get("workspacePath", "")).strip())
        run.workspace = (root / workspace).resolve() if not workspace.is_absolute() else workspace

    env = os.environ.copy()
    env.update(env_updates)
    env["PYTHONUNBUFFERED"] = "1"

    with RUNS_LOCK:
        RUNS[run_id] = run

    target = _run_design_then_runtime if mode == "design_run" else _run_process
    args = (run, env, payload) if mode == "design_run" else (run, env)
    thread = threading.Thread(target=target, args=args, daemon=True)
    thread.start()
    return run


def _run_process(run: RunState, env: dict[str, str]) -> None:
    run.status = "running"
    run.publish({"type": "status", "status": "running"})
    exit_code = _stream_command(run, env, run.command)
    _finish_run(run, exit_code)


def _run_design_then_runtime(run: RunState, env: dict[str, str], payload: dict[str, Any]) -> None:
    run.status = "running"
    run.publish({"type": "status", "status": "running"})
    run.publish({"type": "phase", "line": "Design phase started"})
    design_exit = _stream_command(run, env, run.command)
    if design_exit != 0:
        _finish_run(run, design_exit)
        return

    if run.workspace is None:
        run.workspace = _find_workspace(run.experiment_dir)
    run.publish({"type": "artifacts", "artifacts": _artifact_snapshot(run.workspace)})

    if run.workspace is None:
        run.error = "Design completed but no workspace path was reported"
        run.publish({"type": "error", "line": run.error})
        _finish_run(run, 1)
        return

    runtime_payload = dict(payload)
    runtime_payload["mode"] = "runtime"
    runtime_payload["workspacePath"] = str(run.workspace)
    try:
        runtime_command, runtime_env_updates, runtime_env_flags = _build_runtime_command(runtime_payload)
    except ValueError as exc:
        run.error = str(exc)
        run.publish({"type": "error", "line": str(exc)})
        _finish_run(run, 1)
        return

    env.update(runtime_env_updates)
    run.env_keys.update(runtime_env_flags)
    run.publish({"type": "phase", "line": f"Run phase started: {run.workspace}"})
    run_exit = _stream_command(run, env, runtime_command)
    _finish_run(run, run_exit)


def _stream_command(run: RunState, env: dict[str, str], command: list[str]) -> int:
    run.command = command
    run.publish({"type": "command", "command": _redact_command(command)})
    try:
        process = subprocess.Popen(
            command,
            cwd=run.root,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
    except Exception as exc:
        run.error = str(exc)
        run.publish({"type": "error", "line": str(exc)})
        return 1

    run.process = process
    assert process.stdout is not None

    for raw_line in process.stdout:
        line = raw_line.rstrip("\r\n")
        if not line:
            continue

        experiment_dir = _parse_experiment_dir(run.root, line)
        if experiment_dir is not None:
            run.experiment_dir = experiment_dir

        workspace = _parse_workspace_from_line(run.root, line)
        if workspace is not None:
            run.workspace = workspace

        if run.workspace is None:
            run.workspace = _find_workspace(run.experiment_dir)

        run.publish({
            "type": _event_kind(line),
            "line": line,
            "workspace": str(run.workspace or ""),
            "experimentDir": str(run.experiment_dir or ""),
        })

    exit_code = process.wait()
    run.process = None
    return exit_code


def _finish_run(run: RunState, exit_code: int) -> None:
    run.exit_code = exit_code
    run.ended_at = datetime.now().isoformat(timespec="seconds")
    run.status = "completed" if exit_code == 0 else "failed"
    if run.workspace is None:
        run.workspace = _find_workspace(run.experiment_dir)
    run.publish({"type": "artifacts", "artifacts": _artifact_snapshot(run.workspace)})
    run.publish({"type": "status", "status": run.status, "exitCode": exit_code})


class RunnerHandler(BaseHTTPRequestHandler):
    server_version = "TraceFixRunner/0.1"

    @property
    def root(self) -> Path:
        return self.server.root  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[tracefix-runner] {self.address_string()} - {fmt % args}")

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = unquote(parsed.path)

        if path == "/":
            self._send_file(STATIC_DIR / "index.html")
            return
        if path.startswith("/static/"):
            safe_name = path.removeprefix("/static/").replace("\\", "/")
            if ".." in safe_name.split("/"):
                self._send_error(404, "Not Found")
                return
            self._send_file(STATIC_DIR / safe_name)
            return
        if path == "/api/tasks":
            self._send_json({"tasks": _task_options(self.root)})
            return
        if path.startswith("/api/runs/") and path.endswith("/events"):
            run_id = path.split("/")[3]
            self._stream_events(run_id)
            return
        if path.startswith("/api/runs/"):
            run_id = path.removeprefix("/api/runs/").strip("/")
            run = RUNS.get(run_id)
            if not run:
                self._send_error(404, "Unknown run")
                return
            self._send_json(run.snapshot())
            return
        self._send_error(404, "Not Found")

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/runs":
            payload = self._read_json_body()
            if payload is None:
                self._send_error(400, "Invalid JSON")
                return
            try:
                run = _start_run(self.root, payload)
            except ValueError as exc:
                self._send_error(400, str(exc))
                return
            self._send_json(run.snapshot(), status=201)
            return
        if parsed.path.startswith("/api/runs/") and parsed.path.endswith("/stop"):
            run_id = parsed.path.split("/")[3]
            run = RUNS.get(run_id)
            if not run:
                self._send_error(404, "Unknown run")
                return
            if run.process and run.process.poll() is None:
                run.process.terminate()
                run.publish({"type": "status", "status": "stopping"})
            self._send_json({"ok": True})
            return
        self._send_error(404, "Not Found")

    def _read_json_body(self) -> dict[str, Any] | None:
        length = int(self.headers.get("Content-Length", "0") or 0)
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError:
            return None
        return payload if isinstance(payload, dict) else None

    def _stream_events(self, run_id: str) -> None:
        run = RUNS.get(run_id)
        if not run:
            self._send_error(404, "Unknown run")
            return

        q = run.subscribe()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        try:
            while True:
                try:
                    event = q.get(timeout=15)
                except queue.Empty:
                    self.wfile.write(b": keepalive\n\n")
                    self.wfile.flush()
                    continue
                data = json.dumps(event, ensure_ascii=True)
                self.wfile.write(f"event: tracefix\n".encode("utf-8"))
                self.wfile.write(f"data: {data}\n\n".encode("utf-8"))
                self.wfile.flush()
                if event.get("type") == "status" and event.get("status") in {"completed", "failed"}:
                    break
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            run.unsubscribe(q)

    def _send_file(self, path: Path) -> None:
        if not path.exists() or not path.is_file():
            self._send_error(404, "Not Found")
            return
        data = path.read_bytes()
        content_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _send_json(self, payload: Any, status: int = 200) -> None:
        data = json.dumps(payload, ensure_ascii=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _send_error(self, status: int, message: str) -> None:
        self._send_json({"error": message}, status=status)


def run_server(host: str = "127.0.0.1", port: int = 8788, root: Path | None = None) -> None:
    repo_root = (root or _repo_root()).resolve()
    server = ThreadingHTTPServer((host, port), RunnerHandler)
    server.root = repo_root  # type: ignore[attr-defined]
    print(f"TraceFix Runner running at http://{host}:{port}")
    print(f"Reading repo data from {repo_root}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nTraceFix Runner stopped")
    finally:
        server.server_close()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run the TraceFix LLM runner UI")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8788)
    parser.add_argument("--root", type=Path, default=None)
    args = parser.parse_args(argv)
    run_server(host=args.host, port=args.port, root=args.root)


if __name__ == "__main__":
    main()
