"""Local TraceFix Studio server.

This module intentionally uses only the Python standard library. The UI reads
benchmark definitions and generated fixture artifacts from the current repo.
"""

from __future__ import annotations

import argparse
import json
import mimetypes
import re
import shutil
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

from tracefix.textio import safe_read_json, safe_read_text


STATIC_DIR = Path(__file__).resolve().parent / "static"


def _repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "pyproject.toml").exists():
            return parent
    return Path.cwd()


def _read_json(path: Path) -> Any | None:
    return safe_read_json(path)


def _read_text(path: Path) -> str:
    return safe_read_text(path)


def _task_title(task_id: str, description: str) -> str:
    first_line = next((line.strip() for line in description.splitlines() if line.strip()), "")
    match = re.match(r"^#\s*(.+)$", first_line)
    if match:
        return match.group(1)
    return f"Task {task_id}"


def _difficulty(task_id: str) -> str:
    suffix = task_id[-1:].upper()
    return {"E": "Easy", "M": "Medium", "H": "Hard"}.get(suffix, "Unknown")


def _scenario_number(task_id: str) -> int:
    match = re.match(r"^(\d+)", task_id)
    return int(match.group(1)) if match else 0


def _sort_key(task_id: str) -> tuple[int, int]:
    difficulty_order = {"E": 0, "M": 1, "H": 2}
    return (_scenario_number(task_id), difficulty_order.get(task_id[-1:].upper(), 9))


def _tool_rows(tools: Any, tool_resource_map: dict[str, list[str]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not isinstance(tools, list):
        return rows

    for item in tools:
        fn = item.get("function", {}) if isinstance(item, dict) else {}
        if not isinstance(fn, dict):
            continue
        name = fn.get("name", "unknown")
        rows.append(
            {
                "name": name,
                "description": fn.get("description", ""),
                "agents": fn.get("agent_ids", []),
                "resources": tool_resource_map.get(name, []),
                "can_fail": bool(fn.get("can_fail", False)),
                "required": fn.get("parameters", {}).get("required", []),
            }
        )
    return rows


def _fixture_dir(root: Path, task_id: str) -> Path:
    return root / "tracefix" / "pipeline" / "tests" / "fixtures" / task_id


def _artifact_status(root: Path, task_id: str) -> dict[str, bool]:
    fixture = _fixture_dir(root, task_id)
    return {
        "ir": (fixture / "ir.json").exists(),
        "protocol": (fixture / "Protocol_translated.tla").exists()
        or (fixture / "Protocol.tla").exists(),
        "states": (fixture / "states.json").exists(),
    }


def _extract_goal(description: str) -> str:
    lines = description.splitlines()
    for index, line in enumerate(lines):
        if line.strip().lower() == "## goal":
            body = []
            for next_line in lines[index + 1 :]:
                if next_line.startswith("## "):
                    break
                if next_line.strip():
                    body.append(next_line.strip())
            return " ".join(body)
    return ""


def _build_summaries(root: Path) -> list[dict[str, Any]]:
    desc_root = root / "benchmark" / "descriptions"
    summaries: list[dict[str, Any]] = []
    if not desc_root.exists():
        return summaries

    for task_dir in sorted(desc_root.iterdir(), key=lambda p: _sort_key(p.name)):
        if not task_dir.is_dir():
            continue
        metadata = _read_json(task_dir / "metadata.json") or {}
        tools = _read_json(task_dir / "tools.json") or []
        description = _read_text(task_dir / "description.md")
        tool_resource_map = metadata.get("tool_resource_map", {})
        resource_touch_count = 0
        if isinstance(tool_resource_map, dict):
            resource_touch_count = sum(1 for resources in tool_resource_map.values() if resources)

        summaries.append(
            {
                "id": task_dir.name,
                "title": _task_title(task_dir.name, description),
                "difficulty": _difficulty(task_dir.name),
                "scenario": _scenario_number(task_dir.name),
                "agents": metadata.get("agents", []),
                "resources": metadata.get("resources", []),
                "toolCount": len(tools) if isinstance(tools, list) else 0,
                "resourceTouchCount": resource_touch_count,
                "goal": _extract_goal(description),
                "artifacts": _artifact_status(root, task_dir.name),
            }
        )
    return summaries


def _normalise_endpoints(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    if value is None:
        return []
    return [str(value)]


def _topology_from_fixture(ir: dict[str, Any]) -> dict[str, Any]:
    agents = []
    for agent in ir.get("agents", []):
        agent_id = agent.get("id") if isinstance(agent, dict) else agent
        agents.append({"id": str(agent_id)})

    resources = []
    for resource in ir.get("resources", []):
        resource_id = resource.get("id") if isinstance(resource, dict) else resource
        resource_type = resource.get("type", "Lock") if isinstance(resource, dict) else "Lock"
        resources.append({"id": str(resource_id), "type": resource_type})

    channels = []
    for channel in ir.get("channels", []):
        if not isinstance(channel, dict):
            continue
        channels.append(
            {
                "id": str(channel.get("id", "")),
                "from": _normalise_endpoints(channel.get("from")),
                "to": _normalise_endpoints(channel.get("to")),
                "labels": channel.get("labels", []),
            }
        )
    return {"source": "fixture", "agents": agents, "resources": resources, "channels": channels}


def _topology_from_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    agents = [{"id": str(agent)} for agent in metadata.get("agents", [])]
    resources = [{"id": str(resource), "type": "Lock"} for resource in metadata.get("resources", [])]

    tool_map = metadata.get("tool_resource_map", {})
    resource_links: list[dict[str, Any]] = []
    if isinstance(tool_map, dict):
        for tool_name, touched_resources in tool_map.items():
            if not isinstance(touched_resources, list):
                continue
            for resource_id in touched_resources:
                resource_links.append({"tool": tool_name, "resource": str(resource_id)})

    return {
        "source": "metadata",
        "agents": agents,
        "resources": resources,
        "channels": [],
        "resourceLinks": resource_links,
    }


def _build_task(root: Path, task_id: str) -> dict[str, Any] | None:
    task_dir = root / "benchmark" / "descriptions" / task_id
    if not task_dir.exists():
        return None

    metadata = _read_json(task_dir / "metadata.json") or {}
    tools = _read_json(task_dir / "tools.json") or []
    description = _read_text(task_dir / "description.md")
    tool_resource_map = metadata.get("tool_resource_map", {})

    fixture = _fixture_dir(root, task_id)
    fixture_ir = _read_json(fixture / "ir.json") if (fixture / "ir.json").exists() else None
    protocol_path = (
        fixture / "Protocol_translated.tla"
        if (fixture / "Protocol_translated.tla").exists()
        else fixture / "Protocol.tla"
    )
    protocol_text = _read_text(protocol_path) if protocol_path.exists() else ""
    states = _read_json(fixture / "states.json") if (fixture / "states.json").exists() else None

    topology = (
        _topology_from_fixture(fixture_ir)
        if isinstance(fixture_ir, dict)
        else _topology_from_metadata(metadata)
    )

    return {
        "id": task_id,
        "title": _task_title(task_id, description),
        "difficulty": _difficulty(task_id),
        "scenario": _scenario_number(task_id),
        "description": description,
        "metadata": metadata,
        "tools": _tool_rows(tools, tool_resource_map if isinstance(tool_resource_map, dict) else {}),
        "topology": topology,
        "artifacts": {
            "ir": fixture_ir,
            "protocol": protocol_text,
            "states": states,
            "status": _artifact_status(root, task_id),
        },
        "commands": {
            "pipeline": f"python -m tracefix.pipeline --benchmark {task_id} --verbose",
            "monitoring": (
                "python -m tracefix.runtime.monitoring run "
                f"--task {task_id} --workspace workspace/{task_id} --verbose --live"
            ),
            "enforcement": (
                "python -m tracefix.runtime.enforcement run "
                f"--task {task_id} --workspace workspace/{task_id} --verbose --live"
            ),
        },
    }


def _system_status(root: Path) -> dict[str, Any]:
    jar = root / "lib" / "tla2tools.jar"
    return {
        "python": sys.version.split()[0],
        "java": shutil.which("java") or "",
        "tlaJar": str(jar) if jar.exists() else "",
        "repo": str(root),
        "benchmarkCount": len(_build_summaries(root)),
    }


class TraceFixStudioHandler(BaseHTTPRequestHandler):
    server_version = "TraceFixStudio/0.1"

    @property
    def root(self) -> Path:
        return self.server.root  # type: ignore[attr-defined]

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[tracefix-ui] {self.address_string()} - {fmt % args}")

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
        if path == "/api/summary":
            self._send_json({"tasks": _build_summaries(self.root)})
            return
        if path == "/api/status":
            self._send_json(_system_status(self.root))
            return
        if path.startswith("/api/task/"):
            task_id = path.removeprefix("/api/task/").strip("/")
            if not re.match(r"^\d+[EMH]$", task_id):
                self._send_error(404, "Unknown task")
                return
            task = _build_task(self.root, task_id)
            if not task:
                self._send_error(404, "Unknown task")
                return
            self._send_json(task)
            return

        self._send_error(404, "Not Found")

    def _send_file(self, path: Path) -> None:
        if not path.exists() or not path.is_file():
            self._send_error(404, "Not Found")
            return
        content_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _send_json(self, payload: Any) -> None:
        data = json.dumps(payload, ensure_ascii=True).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _send_error(self, status: int, message: str) -> None:
        data = json.dumps({"error": message}).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def run_server(host: str = "127.0.0.1", port: int = 8787, root: Path | None = None) -> None:
    repo_root = (root or _repo_root()).resolve()
    server = ThreadingHTTPServer((host, port), TraceFixStudioHandler)
    server.root = repo_root  # type: ignore[attr-defined]
    print(f"TraceFix Studio running at http://{host}:{port}")
    print(f"Reading repo data from {repo_root}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nTraceFix Studio stopped")
    finally:
        server.server_close()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Run the local TraceFix Studio UI")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--root", type=Path, default=None)
    args = parser.parse_args(argv)
    run_server(host=args.host, port=args.port, root=args.root)


if __name__ == "__main__":
    main()

