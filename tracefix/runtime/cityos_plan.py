"""Export a verified TraceFix workspace as an intermediary expression.

TraceFix owns planning and verification. This module only summarizes the
verified workspace into an intermediate handoff artifact for the CityOS
Synthesizer. It does not run agents, create Docker containers, or import CityOS.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


PLAN_VERSION = "0.1"


@dataclass(frozen=True)
class CityOSPlanExportResult:
    workspace: Path
    plan_path: Path
    agents: list[str]


def _spec_dir(workspace: Path) -> Path:
    spec = workspace / "spec"
    return spec if spec.is_dir() else workspace


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _rel(workspace: Path, path: Path) -> str:
    try:
        return str(path.relative_to(workspace)).replace("\\", "/")
    except ValueError:
        return str(path).replace("\\", "/")


def _existing_rel(workspace: Path, path: Path) -> str | None:
    return _rel(workspace, path) if path.exists() else None


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _prompt_paths(workspace: Path) -> dict[str, str]:
    for rel in ("prompts/runtime_b", "prompts"):
        prompt_dir = workspace / rel
        if prompt_dir.is_dir():
            prompts = {
                prompt.stem: _rel(workspace, prompt)
                for prompt in sorted(prompt_dir.glob("*.md"))
            }
            if prompts:
                return prompts
    return {}


def _agent_names(ir: dict[str, Any], prompts: dict[str, str]) -> list[str]:
    from_ir = [
        str(agent["id"])
        for agent in ir.get("agents", [])
        if isinstance(agent, dict) and agent.get("id")
    ]
    return from_ir or sorted(prompts)


def _agent_roles(ir: dict[str, Any]) -> dict[str, str]:
    roles: dict[str, str] = {}
    for agent in ir.get("agents", []):
        if not isinstance(agent, dict) or not agent.get("id"):
            continue
        name = str(agent["id"])
        roles[name] = str(agent.get("role") or agent.get("description") or name)
    return roles


def _allowed_edges(ir: dict[str, Any]) -> list[dict[str, Any]]:
    edges = []
    for channel in ir.get("channels", []):
        if not isinstance(channel, dict):
            continue
        edges.append({
            "channel": channel.get("id"),
            "from": channel.get("from"),
            "to": channel.get("to"),
            "labels": channel.get("labels", []),
        })
    return edges


def _allowed_transitions(states: Any) -> list[Any]:
    if not isinstance(states, dict):
        return []
    transitions = states.get("transitions")
    if isinstance(transitions, list):
        return transitions
    state_rows = states.get("states")
    return state_rows if isinstance(state_rows, list) else []


def _verification_status(summary: dict[str, Any], states_path: Path, tlc_error_path: Path) -> str:
    if summary.get("tlc_passed") is True:
        return "verified"
    if summary.get("tlc_passed") is False:
        return "incomplete"
    if tlc_error_path.exists():
        return "incomplete"
    if states_path.exists():
        return "states_extracted"
    return "unknown"


def _application_goals(description: str, ir: dict[str, Any]) -> list[dict[str, str]]:
    goals = [{
        "id": "verified_coordination_blueprint",
        "description": "Preserve the TraceFix-verified protocol and topology.",
    }]
    if description:
        goals.insert(0, {
            "id": "user_intent",
            "description": description,
        })
    if ir.get("resources"):
        goals.append({
            "id": "resource_safety",
            "description": "Honor verified resource constraints and ownership rules.",
        })
    if ir.get("channels"):
        goals.append({
            "id": "communication_safety",
            "description": "Allow only verified agent communication paths.",
        })
    return goals


def _agent_plan(
    name: str,
    *,
    role: str,
    prompt_path: str | None,
    tools: Any,
    channels: list[dict[str, Any]],
    resources: list[Any],
) -> dict[str, Any]:
    incoming = [
        channel for channel in channels
        if isinstance(channel, dict) and channel.get("to") == name
    ]
    outgoing = [
        channel for channel in channels
        if isinstance(channel, dict) and channel.get("from") == name
    ]
    return {
        "name": name,
        "role": role,
        "prompt_path": prompt_path,
        "inputs": [channel.get("id") for channel in incoming if channel.get("id")],
        "outputs": [channel.get("id") for channel in outgoing if channel.get("id")],
        "tools": tools if isinstance(tools, list) else [],
        "required_context": [],
        "concordfs_channels": [
            {
                "channel": channel.get("id"),
                "direction": "inbound",
                "path": f"messages/{name}/inbox/*.json",
            }
            for channel in incoming
            if channel.get("id")
        ] + [
            {
                "channel": channel.get("id"),
                "direction": "outbound",
                "path": f"messages/{name}/outbox/*.json",
            }
            for channel in outgoing
            if channel.get("id")
        ],
        "resources": resources,
        "notes": [
            "CityOS Synthesizer should turn this agent into one CityOS app/container.",
        ],
    }


def build_cityos_module_plan(workspace: Path) -> dict[str, Any]:
    workspace = Path(workspace).expanduser().resolve()
    if not workspace.is_dir():
        raise FileNotFoundError(f"workspace does not exist: {workspace}")

    spec = _spec_dir(workspace)
    ir_path = spec / "ir.json"
    if not ir_path.is_file():
        raise FileNotFoundError("missing required workspace artifact: spec/ir.json")

    ir = _read_json(ir_path, {})
    if not isinstance(ir, dict):
        raise ValueError(f"invalid JSON object: {ir_path}")

    prompts = _prompt_paths(workspace)
    if not prompts:
        raise FileNotFoundError(
            "missing agent prompts: expected prompts/runtime_b/*.md or prompts/*.md"
        )

    states_path = spec / "states.json"
    summary_path = spec / "summary.json"
    tlc_error_path = spec / "tlc_error.md"
    states = _read_json(states_path, {})
    summary = _read_json(summary_path, {})
    tools = _read_json(workspace / "tools.json", [])
    description = _read_text(workspace / "description.md").strip()
    agent_names = _agent_names(ir, prompts)
    roles = _agent_roles(ir)
    channels = ir.get("channels", [])
    resources = ir.get("resources", [])
    allowed_edges = _allowed_edges(ir)

    artifact_paths = {
        "state_machine_path": _existing_rel(workspace, states_path),
        "tla_path": _existing_rel(workspace, spec / "Protocol.tla"),
        "ir_path": _rel(workspace, ir_path),
        "summary_path": _existing_rel(workspace, summary_path),
        "tlc_config_path": _existing_rel(workspace, spec / "Protocol.cfg"),
        "translated_tla_path": _existing_rel(workspace, spec / "Protocol_translated.tla"),
    }

    goals = _application_goals(description, ir)
    topology = {
        "agents": ir.get("agents", []),
        "resources": resources,
        "channels": channels,
    }
    verification_status = _verification_status(summary, states_path, tlc_error_path)
    verification = {
        "status": verification_status,
        "summary": summary,
        "states_available": states_path.exists(),
        "tlc_passed": summary.get("tlc_passed"),
        "tlc_error_path": _rel(workspace, tlc_error_path),
        "production_ready": verification_status == "verified",
    }
    source_artifacts = {
        key: value for key, value in artifact_paths.items() if value
    }

    return {
        "artifact_type": "tracefix_verified_intermediary_expression",
        "version": PLAN_VERSION,
        "application": {
            "name": workspace.name,
            "description": description,
            "goals": goals,
        },
        "goals": goals,
        "tracefix": {
            "workspace_path": str(workspace),
            "verification_status": verification["status"],
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "notes": [
                "TraceFix generated this intermediary expression from verified workspace artifacts.",
                "TraceFix does not run production agents or create Docker containers.",
            ],
        },
        "protocol": {
            "topology": topology,
            "allowed_transitions": _allowed_transitions(states),
            "allowed_communication_edges": allowed_edges,
            **artifact_paths,
        },
        "topology": topology,
        "agents": [
            _agent_plan(
                name,
                role=roles.get(name, name),
                prompt_path=prompts.get(name),
                tools=tools,
                channels=channels if isinstance(channels, list) else [],
                resources=resources if isinstance(resources, list) else [],
            )
            for name in agent_names
        ],
        "communication_requirements": [
            {
                "channel": edge.get("channel"),
                "from": edge.get("from"),
                "to": edge.get("to"),
                "labels": edge.get("labels", []),
                "substrate": "ConcordFS",
                "message_format": "json",
            }
            for edge in allowed_edges
        ],
        "runtime_monitor": {
            "required": True,
            "monitor_rules": [
                "validate_agent_state_transitions",
                "validate_allowed_communication_edges",
                "validate_channel_message_labels",
                "validate_resource_acquire_release_constraints",
                "validate_protocol_completion",
            ],
            "observed_channels": allowed_edges,
            "source_artifacts": [
                path for path in (
                    artifact_paths["state_machine_path"],
                    artifact_paths["tla_path"],
                    artifact_paths["ir_path"],
                    artifact_paths["summary_path"],
                )
                if path
            ],
            "notes": [
                "CityOS Synthesizer should create the runtime monitor as its own CityOS app/container.",
            ],
        },
        "required_external_context": {
            "sensor_context": [],
            "resource_permissions": resources if isinstance(resources, list) else [],
            "notes": [
                "TraceFix cannot infer real CityOS sensor/privacy permissions unless they are declared upstream.",
            ],
        },
        "resource_requirements": resources if isinstance(resources, list) else [],
        "verification": verification,
        "source_artifacts": source_artifacts,
        "cityos_synthesis_handoff": {
            "synthesizer_should_create": [
                "one Dockerized CityOS app per agent",
                "one Dockerized CityOS app for the runtime monitor",
                "ConcordFS channel declarations",
                "CityOS manifests",
                "permission/resource declarations",
            ],
            "tracefix_should_not_create": [
                "production runner",
                "Docker containers",
                "CityOS runtime services",
            ],
            "runtime_owner": "CityOS Runtime OS",
            "synthesis_owner": "CityOS Synthesizer",
        },
        "assumptions": [
            "Agent names are inferred from spec/ir.json, falling back to prompt filenames.",
            "Prompt paths are inferred from prompts/runtime_b/*.md, falling back to prompts/*.md.",
            "External sensor context is left empty unless declared in existing workspace artifacts.",
            "ConcordFS paths are declarations for CityOS/ConcordFS, not a TraceFix runtime dependency.",
        ],
    }


def export_cityos_module_plan(workspace: Path, out: Path | None = None) -> CityOSPlanExportResult:
    workspace = Path(workspace).expanduser().resolve()
    plan = build_cityos_module_plan(workspace)
    spec = _spec_dir(workspace)
    plan_path = Path(out).expanduser().resolve() if out else spec / "cityos_module_plan.json"
    if plan_path.suffix.lower() != ".json":
        plan_path = plan_path / "cityos_module_plan.json"
    _write_json(plan_path, plan)
    return CityOSPlanExportResult(
        workspace=workspace,
        plan_path=plan_path,
        agents=[agent["name"] for agent in plan["agents"]],
    )
