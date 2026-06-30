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
from urllib.parse import parse_qs, unquote, urlparse

from tracefix.runner_ui.tellme_bridge import TellMeBridge
from tracefix.textio import safe_read_json, safe_read_text


STATIC_DIR = Path(__file__).resolve().parent / "static"
UI_BUILD = "tracefix-unified-ui-20260630-tellme-v1"
RUNS: dict[str, "RunState"] = {}
RUNS_LOCK = threading.Lock()


_USAGE_PHASES = ("design", "repair", "verification")
_MODEL_PRICES: dict[str, tuple[float, float]] = {
    "gpt-4.1-mini": (0.40, 1.60),
    "gpt-4.1": (2.00, 8.00),
    "gpt-5-mini": (0.25, 2.00),
    "gpt-5": (1.25, 10.00),
    "claude-sonnet": (3.00, 15.00),
    "claude-sonnet-4": (3.00, 15.00),
    "claude-opus": (15.00, 75.00),
    "claude-opus-4": (15.00, 75.00),
}


def _api_envelope(
    *,
    ok: bool,
    data: Any = None,
    errors: list[str] | None = None,
    warnings: list[str] | None = None,
    artifact_paths: list[str] | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    return {
        "ok": ok,
        "data": data,
        "errors": errors or [],
        "warnings": warnings or [],
        "artifact_paths": artifact_paths or [],
        "run_id": run_id,
    }


def _tellme_bridge(root: Path) -> TellMeBridge:
    return TellMeBridge(root)


def _tracefix_current(root: Path) -> dict[str, Any]:
    current = _tellme_bridge(root).current() or {}
    tracefix = current.get("tracefix") if isinstance(current.get("tracefix"), dict) else {}
    run_id = str(tracefix.get("run_id") or "")
    run = RUNS.get(run_id) if run_id else None
    if run is None:
        return {"run_id": run_id, **tracefix}
    snapshot = run.snapshot()
    if run.workspace is not None:
        _tellme_bridge(root).record_tracefix_workspace(run.id, str(run.workspace))
    return snapshot


def _repo_root() -> Path:
    for parent in Path(__file__).resolve().parents:
        if (parent / "pyproject.toml").exists():
            return parent
    return Path.cwd()


def _ui_state_dir(root: Path) -> Path:
    path = root / ".tracefix-ui"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _model_key(model: str) -> str:
    model = (model or "").strip()
    if "/" in model:
        model = model.split("/")[-1]
    return model.lower()


def _display_model(model: str) -> str:
    model = (model or "").strip() or "unknown"
    if "/" in model:
        model = model.split("/")[-1]
    return model


def _estimate_llm_cost(model: str, input_tokens: int, output_tokens: int) -> tuple[float, bool]:
    key = _model_key(model)
    prices = _MODEL_PRICES.get(key)
    if prices is None:
        for prefix, candidate in _MODEL_PRICES.items():
            if key.startswith(prefix):
                prices = candidate
                break
    if prices is None:
        return 0.0, False
    input_price, output_price = prices
    return round((input_tokens * input_price + output_tokens * output_price) / 1_000_000, 6), True


def _blank_phase() -> dict[str, Any]:
    return {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "estimated_cost_usd": 0.0}


def _usage_payload(model: str = "") -> dict[str, Any]:
    return {
        "model": _display_model(model),
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "estimated_cost_usd": 0.0,
        "cost_known": False,
        "estimated": True,
        "source": "no_usage_metadata",
        "phases": {phase: _blank_phase() for phase in _USAGE_PHASES},
        "session_totals": {"total_runs": 0, "total_tokens": 0, "total_cost_usd": 0.0},
    }


def _load_usage_totals(root: Path) -> dict[str, Any]:
    path = _ui_state_dir(root) / "llm_usage_totals.json"
    data = _read_json(path)
    if not isinstance(data, dict):
        data = {}
    return {
        "total_runs": int(data.get("total_runs") or 0),
        "total_tokens": int(data.get("total_tokens") or 0),
        "total_cost_usd": float(data.get("total_cost_usd") or 0.0),
        "by_model": data.get("by_model") if isinstance(data.get("by_model"), dict) else {},
    }


def _save_usage_totals(root: Path, totals: dict[str, Any]) -> None:
    path = _ui_state_dir(root) / "llm_usage_totals.json"
    path.write_text(json.dumps(totals, indent=2) + "\n", encoding="utf-8")


def _persist_workspace_usage(workspace: Path | None, usage: dict[str, Any], run_id: str, *, final: bool) -> None:
    if workspace is None or not workspace.exists():
        return
    path = workspace / "llm_usage.json"
    existing = _read_json(path)
    if not isinstance(existing, dict):
        existing = {"runs": []}
    existing["current_run"] = {"run_id": run_id, **usage}
    if final:
        runs = existing.get("runs")
        if not isinstance(runs, list):
            runs = []
        runs.append({"run_id": run_id, "ended_at": datetime.now().isoformat(timespec="seconds"), **usage})
        existing["runs"] = runs[-50:]
    path.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")


def _subprocess_python() -> str:
    configured = os.environ.get("TRACEFIX_PYTHON_EXE", "").strip()
    if configured:
        python_path = Path(configured).expanduser()
        if not python_path.exists():
            raise ValueError(f"TRACEFIX_PYTHON_EXE does not exist: {configured}")
        return str(python_path)
    return sys.executable


_DEFAULT_WINDOWS_JAVA17 = Path(r"C:\Program Files\Eclipse Adoptium\jdk-17.0.19.10-hotspot\bin\java.exe")


def _default_java17() -> str:
    return str(_DEFAULT_WINDOWS_JAVA17) if _DEFAULT_WINDOWS_JAVA17.exists() else ""


def _path_beginning(path_value: str, *, max_parts: int = 5) -> str:
    parts = [part for part in path_value.split(os.pathsep) if part]
    return os.pathsep.join(parts[:max_parts])


def _ensure_java_env(env: dict[str, str]) -> dict[str, str]:
    java = (env.get("TLA_VERIFY_JAVA") or env.get("JAVA_EXE") or "").strip()
    default_java = _default_java17()
    if not java and default_java:
        java = default_java
        env["TLA_VERIFY_JAVA"] = java
        env["JAVA_EXE"] = java
    elif java:
        env.setdefault("TLA_VERIFY_JAVA", java)
        env.setdefault("JAVA_EXE", java)

    if java:
        java_path = Path(java)
        if not java_path.exists():
            raise ValueError(f"Configured Java executable does not exist: {java}")
        env.setdefault("JAVA_HOME", str(java_path.parent.parent))
        java_bin = str(java_path.parent)
        path_parts = [part for part in env.get("PATH", "").split(os.pathsep) if part]
        if not any(Path(part) == Path(java_bin) for part in path_parts):
            env["PATH"] = java_bin + os.pathsep + env.get("PATH", "")
    return env


def _selected_java_for_env(env: dict[str, str]) -> str:
    from tracefix.pipeline.pipeline.toolchain import resolve_java

    old_values = {name: os.environ.get(name) for name in ("TLA_VERIFY_JAVA", "JAVA_EXE", "JAVA_HOME", "PATH")}
    try:
        for name in old_values:
            if name in env:
                os.environ[name] = env[name]
            else:
                os.environ.pop(name, None)
        return resolve_java()
    finally:
        for name, value in old_values.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def _run_diagnostics(root: Path, command: list[str], env: dict[str, str]) -> list[str]:
    has_java_arg = "--java-path" in command
    selected_java = _selected_java_for_env(env)
    return [
        f"[tracefix runner] cwd: {root}",
        f"[tracefix runner] Python executable used: {command[0] if command else ''}",
        f"[tracefix runner] command launched: {' '.join(command)}",
        f"[tracefix runner] TLA_VERIFY_JAVA: {env.get('TLA_VERIFY_JAVA', '')}",
        f"[tracefix runner] JAVA_EXE: {env.get('JAVA_EXE', '')}",
        f"[tracefix runner] JAVA_HOME: {env.get('JAVA_HOME', '')}",
        f"[tracefix runner] PATH beginning: {_path_beginning(env.get('PATH', ''))}",
        f"[tracefix runner] --java-path argument passed: {has_java_arg}",
        f"[tracefix runner] final Java selected by TraceFix toolchain: {selected_java}",
    ]


def _read_text(path: Path, limit: int = 180_000) -> str:
    text = safe_read_text(path)
    if len(text) > limit:
        return text[:limit] + "\n\n... truncated in UI ..."
    return text


def _read_json(path: Path) -> Any | None:
    return safe_read_json(path)


def _catalog_has_model(provider: str, model_id: str) -> bool:
    catalog_paths = [
        Path.home() / ".cache" / "opencode" / "models.json",
        Path.home() / ".config" / "opencode" / "models.json",
    ]
    for catalog in catalog_paths:
        data = _read_json(catalog)
        if not isinstance(data, dict):
            continue
        provider_entry = data.get(provider)
        models = provider_entry.get("models") if isinstance(provider_entry, dict) else None
        if isinstance(models, dict) and model_id in models:
            return True
    return False


def _model_options() -> dict[str, list[str]]:
    openrouter = [
        "z-ai/glm-5.2",
        "openai/gpt-4.1-mini",
        "deepseek/deepseek-chat",
        "deepseek/deepseek-r1",
    ]
    if _catalog_has_model("openrouter", "openai/gpt-5.5"):
        openrouter.append("openai/gpt-5.5")
    return {
        "openai": ["gpt-5-mini", "gpt-5", "gpt-4.1-mini", "gpt-4.1"],
        "anthropic": [
            "claude-sonnet-4-5-20250929",
            "claude-opus-4-1-20250805",
            "claude-3-5-haiku-20241022",
        ],
        "openrouter": openrouter,
        "ollama": ["llama3.2:3b", "llama3.1:8b", "qwen2.5-coder:7b", "mistral:7b"],
    }


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
    if _is_incomplete_design_line(clean):
        return "incomplete"
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


def _is_incomplete_design_line(line: str) -> bool:
    clean = line.strip().lower()
    return any(
        marker in clean
        for marker in (
            "tracefix design: incomplete",
            "not runnable yet",
            "spec/tlc_error.md",
        )
    )


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


def _spec_dir(workspace: Path) -> Path:
    spec = workspace / "spec"
    return spec if spec.is_dir() else workspace


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
            "cityosPlan": None,
            "cityosPlanPath": "",
            "tlcErrorPath": "",
            "specDir": "",
            "recovery": None,
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
        "cityosPlan": _read_json(_spec_file(workspace, "cityos_module_plan.json")),
        "cityosPlanPath": str(_spec_file(workspace, "cityos_module_plan.json")),
        "tlcErrorPath": str(_spec_file(workspace, "tlc_error.md")),
        "specDir": str(_spec_dir(workspace)),
        "recovery": _recovery_guidance(workspace),
    }


def _recovery_guidance(workspace: Path) -> dict[str, Any]:
    spec = _spec_dir(workspace)
    ir = _read_json(spec / "ir.json")
    protocol_exists = (spec / "Protocol.tla").exists()
    channels = ir.get("channels", []) if isinstance(ir, dict) else []
    notes = [
        "Do not treat this workspace as production-ready until TLC passes.",
        "Inspect tlc_error.md first; then fix the IR/Protocol or rerun design with a clearer task.",
    ]
    if isinstance(ir, dict) and not protocol_exists:
        notes = [
            "Design stopped before PlusCal scaffolding. TLC did not run.",
            "Likely cause: incomplete IR, schema mismatch, or missing communication channels.",
            f"Channels detected in ir.json: {len(channels)}.",
            "Fix the IR or rerun design; Protocol.tla must exist before TLC can produce tlc_error.md.",
            "Do not treat this workspace as production-ready until TLC passes.",
        ]
    return {
        "workspace": str(workspace),
        "tlcErrorPath": str(spec / "tlc_error.md"),
        "irPath": str(spec / "ir.json"),
        "protocolPath": str(spec / "Protocol.tla"),
        "rerunDesignCommand": f"tracefix design --name {workspace.name} \"<same task or corrected task>\"",
        "manualCommands": [
            f"tla-verify-pluscal validate \"{spec / 'ir.json'}\"",
            f"tla-verify-pluscal scaffold \"{spec / 'ir.json'}\" -o \"{spec}\"",
            f"tla-verify-pluscal verify \"{spec}\"",
        ],
        "notes": notes,
    }


def _workspace_from_payload(root: Path, payload: dict[str, Any]) -> Path:
    raw = str(payload.get("workspacePath") or payload.get("workspace") or "").strip()
    if not raw:
        raise ValueError("Workspace path is required")
    workspace = Path(raw)
    return (root / workspace).resolve() if not workspace.is_absolute() else workspace


def _export_intermediary_plan(workspace: Path) -> Path:
    from tracefix.runtime.cityos_plan import export_cityos_module_plan

    return export_cityos_module_plan(workspace).plan_path


def _default_cityos_root() -> Path | None:
    configured = os.environ.get("CITYOS_ROOT", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    candidate = Path.home() / "TraceFix-CityOS" / "GitHub" / "cityos"
    if candidate.exists():
        return candidate.resolve()
    candidate = Path.home() / "cityos"
    return candidate.resolve() if candidate.exists() else None


def _synth_apps_dir(payload: dict[str, Any], workspace: Path) -> Path:
    raw_apps = str(payload.get("appsDir") or "").strip()
    if raw_apps:
        return Path(raw_apps).expanduser().resolve()
    raw_cityos = str(payload.get("cityosRoot") or "").strip()
    if raw_cityos:
        return (Path(raw_cityos).expanduser() / "apps").resolve()
    return (workspace / "output" / "cityos_synthesis").resolve()


def _synth_file_requirements(workspace: Path) -> list[dict[str, Any]]:
    spec = _spec_dir(workspace)
    requirements = [
        ("spec/cityos_module_plan.json", spec / "cityos_module_plan.json", "Generate Verified Plan or Export Intermediary Plan"),
        ("spec/ir.json", spec / "ir.json", "Generate Verified Plan"),
        ("spec/states.json", spec / "states.json", "Run TraceFix verification until TLC/state extraction completes"),
        ("spec/summary.json", spec / "summary.json", "Run TraceFix verification until summary is written"),
        ("spec/Protocol.tla", spec / "Protocol.tla", "Run TraceFix protocol generation"),
        ("spec/Protocol.cfg", spec / "Protocol.cfg", "Run TraceFix protocol generation"),
    ]
    translated = spec / "Protocol_translated.tla"
    items = [
        {
            "path": label,
            "absolutePath": str(path),
            "exists": path.exists(),
            "createdBy": created_by,
            "required": True,
        }
        for label, path, created_by in requirements
    ]
    items.append({
        "path": "spec/Protocol_translated.tla",
        "absolutePath": str(translated),
        "exists": translated.exists(),
        "createdBy": "PlusCal translation",
        "required": False,
    })
    return items


def _synth_workspace_summary(workspace: Path) -> dict[str, Any]:
    spec = _spec_dir(workspace)
    plan_path = spec / "cityos_module_plan.json"
    plan = _read_json(plan_path)
    ir = _read_json(spec / "ir.json")
    summary = _read_json(spec / "summary.json")
    verification = plan.get("verification", {}) if isinstance(plan, dict) else {}
    agents = plan.get("agents", []) if isinstance(plan, dict) and isinstance(plan.get("agents"), list) else []
    topology = plan.get("topology", {}) if isinstance(plan, dict) and isinstance(plan.get("topology"), dict) else {}
    if not topology and isinstance(plan, dict):
        protocol = plan.get("protocol", {})
        topology = protocol.get("topology", {}) if isinstance(protocol, dict) else {}
    if not topology and isinstance(ir, dict):
        topology = {
            "agents": ir.get("agents", []),
            "channels": ir.get("channels", []),
            "resources": ir.get("resources", []),
        }
    requirements = _synth_file_requirements(workspace)
    missing_required = [item for item in requirements if item["required"] and not item["exists"]]
    return {
        "name": workspace.name,
        "path": str(workspace),
        "lastModified": workspace.stat().st_mtime if workspace.exists() else None,
        "planPath": str(plan_path),
        "hasPlan": plan_path.exists(),
        "productionReady": verification.get("production_ready") is True,
        "verificationStatus": verification.get("status") or (
            "verified" if isinstance(summary, dict) and summary.get("tlc_passed") is True else "unknown"
        ),
        "agents": [
            agent.get("name") or agent.get("id")
            for agent in agents
            if isinstance(agent, dict)
        ],
        "channels": topology.get("channels", []) if isinstance(topology, dict) else [],
        "resources": topology.get("resources", []) if isinstance(topology, dict) else [],
        "requirements": requirements,
        "missingRequired": missing_required,
        "outputDir": str((workspace / "output" / "cityos_synthesis").resolve()),
        "ready": not missing_required and verification.get("production_ready") is True,
    }


def _synth_workspace_options(root: Path) -> list[dict[str, Any]]:
    workspace_root = root / "workspace"
    if not workspace_root.exists():
        return []
    workspaces = [path for path in workspace_root.iterdir() if path.is_dir()]
    workspaces.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    return [_synth_workspace_summary(path) for path in workspaces[:80]]


def _run_cityos_synthesis(root: Path, payload: dict[str, Any]) -> dict[str, Any]:
    from tracefix.runtime.cityos_synthesizer import synthesize_cityos_apps

    workspace = _workspace_from_payload(root, payload)
    if not workspace.exists():
        raise FileNotFoundError(f"Workspace does not exist: {workspace}")
    summary = _synth_workspace_summary(workspace)
    missing = summary.get("missingRequired") or []
    if missing:
        details = "; ".join(
            f"{item.get('path')} (created by: {item.get('createdBy')})"
            for item in missing
            if isinstance(item, dict)
        )
        raise FileNotFoundError(f"Workspace is missing required synthesis artifacts: {details}")
    apps_dir = _synth_apps_dir(payload, workspace)
    package_name = str(payload.get("packageName") or "").strip() or None
    overwrite = bool(payload.get("overwrite"))
    result = synthesize_cityos_apps(
        workspace,
        apps_dir=apps_dir,
        package_name=package_name,
        overwrite=overwrite,
    )
    return {
        "workspace": str(result.workspace),
        "planPath": str(result.plan_path),
        "appsDir": str(result.apps_dir),
        "manifestPath": str(result.manifest_path),
        "outputDir": str(result.apps_dir),
        "apps": [
            {
                "name": app.name,
                "kind": app.kind,
                "agent": app.agent,
                "path": str(app.path),
                "buildCommand": f"just build {app.name}",
            }
            for app in result.apps
        ],
        "summary": _synth_workspace_summary(result.workspace),
    }


def _open_local_path(path: Path) -> None:
    if not path.exists():
        raise FileNotFoundError(f"Path does not exist: {path}")
    if sys.platform.startswith("win"):
        os.startfile(path)  # type: ignore[attr-defined]
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


@dataclass
class UsageState:
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    exact_cost_usd: float | None = None
    cost_known: bool = False
    source: str = "no_usage_metadata"
    active_phase: str = "design"
    phases: dict[str, dict[str, Any]] = field(default_factory=lambda: {phase: _blank_phase() for phase in _USAGE_PHASES})

    def set_model(self, model: str) -> None:
        if model:
            self.model = _display_model(model)

    def set_phase_from_line(self, line: str) -> None:
        clean = line.lower()
        if "repair" in clean:
            self.active_phase = "repair"
        elif any(marker in clean for marker in ("tlc", "pluscal", "verify", "verification")):
            self.active_phase = "verification"
        elif "design" in clean or "ir" in clean or "protocol" in clean:
            self.active_phase = "design"

    def add_usage(
        self,
        *,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cost_usd: float | None = None,
        model: str = "",
        source: str = "usage_metadata",
    ) -> bool:
        input_tokens = max(0, int(input_tokens or 0))
        output_tokens = max(0, int(output_tokens or 0))
        if input_tokens == 0 and output_tokens == 0 and cost_usd is None:
            return False
        self.set_model(model)
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens
        if cost_usd is not None:
            try:
                parsed_cost = float(cost_usd)
            except (TypeError, ValueError):
                parsed_cost = 0.0
            self.exact_cost_usd = (self.exact_cost_usd or 0.0) + parsed_cost
            self.cost_known = True
        self.source = source

        phase = self.phases.setdefault(self.active_phase, _blank_phase())
        phase["input_tokens"] += input_tokens
        phase["output_tokens"] += output_tokens
        phase["total_tokens"] = phase["input_tokens"] + phase["output_tokens"]
        phase_cost, known = _estimate_llm_cost(self.model, phase["input_tokens"], phase["output_tokens"])
        phase["estimated_cost_usd"] = phase_cost
        phase["cost_known"] = known
        return True

    def merge_session_stats(self, session: Any) -> bool:
        if not isinstance(session, dict):
            return False
        stats = session.get("stats")
        if not isinstance(stats, dict):
            return False
        config = session.get("config") if isinstance(session.get("config"), dict) else {}
        model = str(config.get("model") or self.model or "")
        input_tokens = int(stats.get("prompt_tokens") or 0)
        output_tokens = int(stats.get("completion_tokens") or 0)
        cost = stats.get("estimated_cost_usd")
        if input_tokens < self.input_tokens or output_tokens < self.output_tokens:
            return False
        self.set_model(model)
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        if cost is not None:
            self.exact_cost_usd = float(cost)
            self.cost_known = bool(stats.get("cost_known", True))
        self.source = "session_json"
        phase = self.phases.setdefault("design", _blank_phase())
        phase["input_tokens"] = input_tokens
        phase["output_tokens"] = output_tokens
        phase["total_tokens"] = input_tokens + output_tokens
        phase["estimated_cost_usd"] = float(cost or _estimate_llm_cost(self.model, input_tokens, output_tokens)[0])
        phase["cost_known"] = self.cost_known
        return True

    def parse_line(self, line: str) -> bool:
        self.set_phase_from_line(line)
        parsed = _parse_usage_line(line)
        if not parsed:
            return False
        return self.add_usage(**parsed)

    def snapshot(self, session_totals: dict[str, Any] | None = None) -> dict[str, Any]:
        total = self.input_tokens + self.output_tokens
        estimated_cost, price_known = _estimate_llm_cost(self.model, self.input_tokens, self.output_tokens)
        if self.exact_cost_usd is not None:
            cost = round(self.exact_cost_usd, 6)
            estimated = False
            known = self.cost_known
        else:
            cost = estimated_cost
            estimated = True
            known = price_known
        data = _usage_payload(self.model)
        data.update({
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": total,
            "estimated_cost_usd": cost,
            "cost_known": known,
            "estimated": estimated,
            "source": self.source,
            "phases": self.phases,
        })
        if session_totals:
            data["session_totals"] = {
                "total_runs": int(session_totals.get("total_runs") or 0),
                "total_tokens": int(session_totals.get("total_tokens") or 0),
                "total_cost_usd": round(float(session_totals.get("total_cost_usd") or 0.0), 6),
            }
        return data


def _parse_usage_line(line: str) -> dict[str, Any] | None:
    stripped = line.strip()
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError:
        data = None
    if isinstance(data, dict):
        input_tokens = data.get("input_tokens") or data.get("prompt_tokens")
        output_tokens = data.get("output_tokens") or data.get("completion_tokens")
        if input_tokens is not None or output_tokens is not None:
            return {
                "input_tokens": int(input_tokens or 0),
                "output_tokens": int(output_tokens or 0),
                "cost_usd": data.get("cost") or data.get("cost_usd") or data.get("estimated_cost_usd"),
                "model": str(data.get("model") or ""),
                "source": "json_usage_event",
            }

    match = re.search(r"([\d,]+)\s+in\s+\+\s+([\d,]+)\s+out", stripped, re.IGNORECASE)
    if match:
        return {
            "input_tokens": int(match.group(1).replace(",", "")),
            "output_tokens": int(match.group(2).replace(",", "")),
            "source": "stdout_usage_summary",
        }

    match = re.search(
        r"([\d,]+)\s+tok\s+\(([\d,]+)\s+in\s*/\s*([\d,]+)\s+out.*?\$([0-9.]+)",
        stripped,
        re.IGNORECASE,
    )
    if match:
        return {
            "input_tokens": int(match.group(2).replace(",", "")),
            "output_tokens": int(match.group(3).replace(",", "")),
            "cost_usd": float(match.group(4)),
            "source": "stdout_cost_summary",
        }
    return None


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
    verification_incomplete: bool = False
    usage: UsageState = field(default_factory=UsageState)
    usage_finalized: bool = False
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
        self.refresh_usage_from_workspace()
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
            "usage": self.usage.snapshot(_load_usage_totals(self.root)),
            "error": self.error,
            "verificationIncomplete": self.verification_incomplete,
        }

    def refresh_usage_from_workspace(self) -> bool:
        if self.workspace is None:
            return False
        session = _read_json(self.workspace / "session.json")
        changed = self.usage.merge_session_stats(session)
        if changed:
            _persist_workspace_usage(self.workspace, self.usage.snapshot(_load_usage_totals(self.root)), self.id, final=False)
        return changed

    def publish_usage(self) -> None:
        totals = _load_usage_totals(self.root)
        usage = self.usage.snapshot(totals)
        _persist_workspace_usage(self.workspace, usage, self.id, final=False)
        self.publish({"type": "usage", "usage": usage})

    def finalize_usage(self) -> None:
        if self.usage_finalized:
            return
        self.refresh_usage_from_workspace()
        usage = self.usage.snapshot(_load_usage_totals(self.root))
        totals = _load_usage_totals(self.root)
        totals["total_runs"] = int(totals.get("total_runs") or 0) + 1
        totals["total_tokens"] = int(totals.get("total_tokens") or 0) + int(usage.get("total_tokens") or 0)
        totals["total_cost_usd"] = round(float(totals.get("total_cost_usd") or 0.0) + float(usage.get("estimated_cost_usd") or 0.0), 6)
        by_model = totals.get("by_model") if isinstance(totals.get("by_model"), dict) else {}
        model_key = _display_model(str(usage.get("model") or "unknown"))
        model_totals = by_model.setdefault(model_key, {"runs": 0, "tokens": 0, "cost_usd": 0.0})
        model_totals["runs"] = int(model_totals.get("runs") or 0) + 1
        model_totals["tokens"] = int(model_totals.get("tokens") or 0) + int(usage.get("total_tokens") or 0)
        model_totals["cost_usd"] = round(float(model_totals.get("cost_usd") or 0.0) + float(usage.get("estimated_cost_usd") or 0.0), 6)
        totals["by_model"] = by_model
        _save_usage_totals(self.root, totals)
        self.usage_finalized = True
        _persist_workspace_usage(self.workspace, self.usage.snapshot(totals), self.id, final=True)


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
        _subprocess_python(),
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
        _subprocess_python(),
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
    if payload.get("legacyDebugView", False):
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
        _subprocess_python(),
        "-u",
        "-B",
        "-m",
        "tracefix.runtime.cli",
        "run",
        "--local-dev",
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
    elif mode == "plan":
        workspace = _workspace_from_payload(root, payload)
        command = [
            _subprocess_python(),
            "-u",
            "-B",
            "-m",
            "tracefix.runtime.cli",
            "export-cityos-plan",
            "--workspace",
            str(workspace),
        ]
        env_updates = {}
        env_flags = {}
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
    run.usage.set_model(str(payload.get("model", "")))
    resolved_model = _model_for_tracefix(payload)
    run.publish({
        "type": "log",
        "line": (
            f"TraceFix model routing: requested_provider={provider} "
            f"requested_model={str(payload.get('model', '')).strip() or '(none)'} "
            f"resolved_model={resolved_model or '(none)'} "
            f"actual_model_passed_to_opencode={resolved_model or '(none)'}"
        ),
    })
    if mode == "runtime":
        workspace = Path(str(payload.get("workspacePath", "")).strip())
        run.workspace = (root / workspace).resolve() if not workspace.is_absolute() else workspace
    if mode == "plan":
        run.workspace = _workspace_from_payload(root, payload)

    env = os.environ.copy()
    env.update(env_updates)
    env = _ensure_java_env(env)
    env["PYTHONUNBUFFERED"] = "1"

    with RUNS_LOCK:
        RUNS[run_id] = run

    target = _run_plan_export if mode == "plan" else (_run_design_then_runtime if mode == "design_run" else _run_process)
    args = (run, env, payload) if mode == "design_run" else (run, env)
    thread = threading.Thread(target=target, args=args, daemon=True)
    thread.start()
    return run


def _run_plan_export(run: RunState, env: dict[str, str]) -> None:
    del env
    run.status = "running"
    run.publish({"type": "status", "status": "running"})
    run.publish_usage()
    run.publish({"type": "phase", "line": "Exporting verified intermediary expression"})
    try:
        if run.workspace is None:
            raise ValueError("Workspace path is required")
        plan_path = _export_intermediary_plan(run.workspace)
        run.publish({"type": "workspace", "line": f"Intermediary plan: {plan_path}", "workspace": str(run.workspace)})
        run.publish({"type": "artifacts", "artifacts": _artifact_snapshot(run.workspace)})
        _finish_run(run, 0)
    except Exception as exc:  # noqa: BLE001 - local UI should surface clear errors
        run.error = str(exc)
        run.publish({"type": "error", "line": str(exc)})
        _finish_run(run, 1)


def _run_process(run: RunState, env: dict[str, str]) -> None:
    run.status = "running"
    run.publish({"type": "status", "status": "running"})
    run.publish_usage()
    exit_code = _stream_command(run, env, run.command)
    _finish_run(run, exit_code)


def _run_design_then_runtime(run: RunState, env: dict[str, str], payload: dict[str, Any]) -> None:
    run.status = "running"
    run.publish({"type": "status", "status": "running"})
    run.publish_usage()
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
        for line in _run_diagnostics(run.root, command, env):
            run.publish({"type": "phase", "line": line})
    except Exception as exc:
        run.error = str(exc)
        run.publish({"type": "error", "line": str(exc)})
        return 1
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
        if _is_incomplete_design_line(line):
            run.verification_incomplete = True
        if run.usage.parse_line(line):
            run.publish_usage()
        elif run.refresh_usage_from_workspace():
            run.publish_usage()

    exit_code = process.wait()
    run.process = None
    return exit_code


def _finish_run(run: RunState, exit_code: int) -> None:
    run.exit_code = exit_code
    run.ended_at = datetime.now().isoformat(timespec="seconds")
    run.status = "verification_incomplete" if run.verification_incomplete else (
        "completed" if exit_code == 0 else "failed"
    )
    if run.workspace is None:
        run.workspace = _find_workspace(run.experiment_dir)
    if run.workspace is not None:
        _tellme_bridge(run.root).record_tracefix_workspace(run.id, str(run.workspace))
    run.finalize_usage()
    run.publish({"type": "artifacts", "artifacts": _artifact_snapshot(run.workspace)})
    run.publish_usage()
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
        if path == "/api/health":
            self._send_json(_api_envelope(
                ok=True,
                data={
                    "service": "tracefix-unified-ui",
                    "status": "healthy",
                    "port": self.server.server_port,
                    "repo_root": str(self.root.resolve()),
                    "ui_build": UI_BUILD,
                },
            ))
            return
        if path == "/api/tellme/current":
            bridge = _tellme_bridge(self.root)
            current = bridge.current()
            data = current.get("tellme") if current else None
            self._send_json(_api_envelope(
                ok=data is not None,
                data=data,
                errors=[] if data is not None else ["No TeLLMe run is available yet."],
                warnings=list(data.get("warnings") or []) if isinstance(data, dict) else [],
                artifact_paths=bridge.artifact_paths(data) if isinstance(data, dict) else [],
                run_id=str(current.get("run_id")) if current else None,
            ))
            return
        if path == "/api/tracefix/current":
            data = _tracefix_current(self.root)
            run_id = str(data.get("id") or data.get("run_id") or "")
            workspace = str(
                data.get("workspace")
                or ((data.get("artifacts") or {}).get("workspace") if isinstance(data.get("artifacts"), dict) else "")
                or ""
            )
            artifact_paths = []
            if workspace:
                artifact_paths = (_artifact_snapshot(Path(workspace)).get("files") or [])
            self._send_json(_api_envelope(
                ok=bool(run_id),
                data=data,
                errors=[] if run_id else ["No TraceFix run has been started from TeLLMe."],
                artifact_paths=artifact_paths,
                run_id=run_id or None,
            ))
            return
        if path == "/api/cityos/current":
            current = _tellme_bridge(self.root).current() or {}
            tracefix = current.get("tracefix") if isinstance(current.get("tracefix"), dict) else {}
            cityos = current.get("cityos") if isinstance(current.get("cityos"), dict) else {}
            workspace_raw = str(tracefix.get("workspace") or "")
            summary = None
            if workspace_raw and Path(workspace_raw).exists():
                summary = _synth_workspace_summary(Path(workspace_raw))
            data = {"result": cityos, "workspace": summary}
            self._send_json(_api_envelope(
                ok=bool(summary or cityos),
                data=data,
                errors=[] if summary or cityos else ["No verified TraceFix workspace is available for synthesis."],
                run_id=str(current.get("run_id") or "") or None,
            ))
            return
        if path == "/api/ui-info":
            self._send_json({
                "build": UI_BUILD,
                "static_dir": str(STATIC_DIR.resolve()),
                "repo_root": str(self.root.resolve()),
            })
            return
        if path == "/api/model-options":
            self._send_json({"models": _model_options()})
            return
        if path == "/api/tasks":
            self._send_json({"tasks": _task_options(self.root)})
            return
        if path == "/api/synth/config":
            cityos_root = _default_cityos_root()
            self._send_json({
                "repoRoot": str(self.root.resolve()),
                "cityosRoot": str(cityos_root) if cityos_root is not None else "",
                "appsDir": str((cityos_root / "apps").resolve()) if cityos_root is not None else "",
                "workspaces": _synth_workspace_options(self.root),
            })
            return
        if path == "/api/synth/workspaces":
            self._send_json({"workspaces": _synth_workspace_options(self.root)})
            return
        if path == "/api/synth/workspace":
            query = parse_qs(parsed.query)
            workspace_raw = (query.get("workspace") or [""])[0]
            try:
                workspace = _workspace_from_payload(self.root, {"workspacePath": workspace_raw})
                if not workspace.exists():
                    raise FileNotFoundError(f"Workspace does not exist: {workspace}")
            except Exception as exc:  # noqa: BLE001 - local UI should surface clear errors
                self._send_error(400, str(exc))
                return
            self._send_json({"workspace": _synth_workspace_summary(workspace)})
            return
        if path == "/api/intermediary-plan":
            query = parse_qs(parsed.query)
            workspace_raw = (query.get("workspace") or [""])[0]
            try:
                workspace = _workspace_from_payload(self.root, {"workspacePath": workspace_raw})
            except ValueError as exc:
                self._send_error(400, str(exc))
                return
            self._send_json({
                "workspace": str(workspace),
                "planPath": str(_spec_file(workspace, "cityos_module_plan.json")),
                "plan": _read_json(_spec_file(workspace, "cityos_module_plan.json")),
            })
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
        if parsed.path == "/api/tellme/query":
            payload = self._read_json_body()
            if payload is None:
                self._send_json(_api_envelope(ok=False, errors=["Invalid JSON request body."]), status=400)
                return
            try:
                bridge = _tellme_bridge(self.root)
                data = bridge.process_query(
                    query=str(payload.get("query") or payload.get("user_query") or ""),
                    space_id=str(payload.get("space_id") or "").strip() or None,
                    timestamp=str(payload.get("timestamp") or "").strip() or None,
                    backend_mode=str(payload.get("backend_mode") or "deterministic"),
                    llm_api_key=str(payload.get("llm_api_key") or "").strip() or None,
                    llm_model=str(payload.get("llm_model") or "gpt-4.1-mini"),
                )
            except Exception as exc:  # noqa: BLE001 - API must surface TeLLMe failures
                self._send_json(
                    _api_envelope(ok=False, errors=[f"{type(exc).__name__}: {exc}"]),
                    status=400,
                )
                return
            self._send_json(_api_envelope(
                ok=True,
                data=data,
                warnings=list(data.get("warnings") or []),
                artifact_paths=bridge.artifact_paths(data),
                run_id=str(data.get("query_id") or ""),
            ), status=201)
            return
        if parsed.path == "/api/tracefix/from-tellme":
            payload = self._read_json_body()
            if payload is None:
                self._send_json(_api_envelope(ok=False, errors=["Invalid JSON request body."]), status=400)
                return
            try:
                bridge = _tellme_bridge(self.root)
                task_text = bridge.tracefix_task_text()
                run_payload = dict(payload)
                run_payload.update({
                    "mode": "design",
                    "taskMode": "custom",
                    "customTask": task_text,
                })
                run = _start_run(self.root, run_payload)
                bridge.record_tracefix_run(run.id)
            except Exception as exc:  # noqa: BLE001 - API must surface handoff failures
                self._send_json(
                    _api_envelope(ok=False, errors=[f"{type(exc).__name__}: {exc}"]),
                    status=400,
                )
                return
            self._send_json(_api_envelope(
                ok=True,
                data=run.snapshot(),
                run_id=run.id,
            ), status=201)
            return
        if parsed.path == "/api/cityos/synthesize":
            payload = self._read_json_body()
            if payload is None:
                self._send_json(_api_envelope(ok=False, errors=["Invalid JSON request body."]), status=400)
                return
            try:
                current = _tellme_bridge(self.root).current() or {}
                tracefix = current.get("tracefix") if isinstance(current.get("tracefix"), dict) else {}
                synth_payload = dict(payload)
                if not synth_payload.get("workspace") and not synth_payload.get("workspacePath"):
                    synth_payload["workspace"] = tracefix.get("workspace")
                result = _run_cityos_synthesis(self.root, synth_payload)
                _tellme_bridge(self.root).record_cityos_result(result)
            except Exception as exc:  # noqa: BLE001 - API must surface synthesis failures
                self._send_json(
                    _api_envelope(ok=False, errors=[f"{type(exc).__name__}: {exc}"]),
                    status=400,
                )
                return
            self._send_json(_api_envelope(
                ok=True,
                data=result,
                artifact_paths=[
                    str(result.get("manifestPath") or ""),
                    *[str(app.get("path") or "") for app in result.get("apps") or []],
                ],
                run_id=str(current.get("run_id") or "") or None,
            ))
            return
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
        if parsed.path == "/api/export-intermediary-plan":
            payload = self._read_json_body()
            if payload is None:
                self._send_error(400, "Invalid JSON")
                return
            try:
                workspace = _workspace_from_payload(self.root, payload)
                plan_path = _export_intermediary_plan(workspace)
            except Exception as exc:  # noqa: BLE001 - local UI should surface clear errors
                self._send_error(400, str(exc))
                return
            self._send_json({
                "ok": True,
                "workspace": str(workspace),
                "planPath": str(plan_path),
                "artifacts": _artifact_snapshot(workspace),
            })
            return
        if parsed.path == "/api/synth/synthesize":
            payload = self._read_json_body()
            if payload is None:
                self._send_error(400, "Invalid JSON")
                return
            try:
                result = _run_cityos_synthesis(self.root, payload)
            except Exception as exc:  # noqa: BLE001 - local UI should surface clear errors
                self._send_error(400, str(exc))
                return
            self._send_json(result)
            return
        if parsed.path == "/api/open-workspace":
            payload = self._read_json_body()
            if payload is None:
                self._send_error(400, "Invalid JSON")
                return
            try:
                workspace = _workspace_from_payload(self.root, payload)
                if not workspace.exists():
                    raise FileNotFoundError(f"Workspace does not exist: {workspace}")
                _open_local_path(workspace)
            except Exception as exc:  # noqa: BLE001 - local UI should surface clear errors
                self._send_error(400, str(exc))
                return
            self._send_json({"ok": True, "workspace": str(workspace)})
            return
        if parsed.path == "/api/open-artifact":
            payload = self._read_json_body()
            if payload is None:
                self._send_error(400, "Invalid JSON")
                return
            try:
                workspace = _workspace_from_payload(self.root, payload)
                target = str(payload.get("target", "")).strip()
                if target == "tlc_error":
                    path = _spec_file(workspace, "tlc_error.md")
                elif target == "spec_dir":
                    path = _spec_dir(workspace)
                else:
                    raise ValueError("Unknown artifact target")
                _open_local_path(path)
            except Exception as exc:  # noqa: BLE001 - local UI should surface clear errors
                self._send_error(400, str(exc))
                return
            self._send_json({"ok": True, "path": str(path)})
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
                if event.get("type") == "status" and event.get("status") in {"completed", "failed", "verification_incomplete"}:
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
    subprocess_python = _subprocess_python()
    startup_env = _ensure_java_env(os.environ.copy())
    server = ThreadingHTTPServer((host, port), RunnerHandler)
    server.root = repo_root  # type: ignore[attr-defined]
    print(f"TraceFix Intermediary Planner running at http://{host}:{port}", flush=True)
    print(f"Reading repo data from {repo_root}", flush=True)
    print(f"Serving UI static files from {STATIC_DIR.resolve()}", flush=True)
    print(f"TraceFix UI build: {UI_BUILD}", flush=True)
    print(f"TraceFix UI Python: {sys.executable}")
    print(f"TraceFix subprocess Python: {subprocess_python}")
    print(f"TraceFix TLA_VERIFY_JAVA: {startup_env.get('TLA_VERIFY_JAVA', '')}")
    print(f"TraceFix JAVA_EXE: {startup_env.get('JAVA_EXE', '')}")
    print(f"TraceFix JAVA_HOME: {startup_env.get('JAVA_HOME', '')}")
    print(f"TraceFix PATH beginning: {_path_beginning(startup_env.get('PATH', ''))}")
    print(f"TraceFix final Java selected by toolchain: {_selected_java_for_env(startup_env)}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nTraceFix Intermediary Planner stopped")
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
