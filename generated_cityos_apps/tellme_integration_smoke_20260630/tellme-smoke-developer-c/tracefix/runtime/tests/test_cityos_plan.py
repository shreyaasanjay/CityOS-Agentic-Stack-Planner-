from __future__ import annotations

import json

import pytest

from tracefix.runtime.cityos_plan import export_cityos_module_plan


def test_export_cityos_module_plan_creates_verified_overview(tmp_path):
    workspace = tmp_path / "workspace"
    (workspace / "spec").mkdir(parents=True)
    (workspace / "description.md").write_text("Build a two-agent review workflow.\n")
    (workspace / "spec" / "ir.json").write_text(json.dumps({
        "agents": [{"id": "WRITER"}, {"id": "EDITOR"}],
        "resources": [{"id": "DOC", "type": "Lock"}],
        "channels": [
            {
                "id": "writer_to_editor",
                "from": "WRITER",
                "to": "EDITOR",
                "labels": ["submit"],
            },
        ],
    }))
    (workspace / "spec" / "states.json").write_text(json.dumps({
        "initial_states": {"WRITER": "w0", "EDITOR": "e0"},
        "transitions": [{"agent": "WRITER", "from": "w0", "to": "w1"}],
    }))
    (workspace / "spec" / "summary.json").write_text(json.dumps({"tlc_passed": True}))
    (workspace / "prompts" / "runtime_b").mkdir(parents=True)
    (workspace / "prompts" / "runtime_b" / "WRITER.md").write_text("# writer\n")
    (workspace / "prompts" / "runtime_b" / "EDITOR.md").write_text("# editor\n")

    result = export_cityos_module_plan(workspace)

    assert result.plan_path == workspace / "spec" / "cityos_module_plan.json"
    plan = json.loads(result.plan_path.read_text())
    assert plan["artifact_type"] == "tracefix_verified_intermediary_expression"
    assert plan["tracefix"]["verification_status"] == "verified"
    assert plan["verification"]["production_ready"] is True
    assert plan["verification"]["tlc_error_path"] == "spec/tlc_error.md"
    assert "application" in plan
    assert "goals" in plan
    assert "protocol" in plan
    assert "topology" in plan
    assert "agents" in plan
    assert "communication_requirements" in plan
    assert "runtime_monitor" in plan
    assert "resource_requirements" in plan
    assert "verification" in plan
    assert "source_artifacts" in plan
    assert "cityos_synthesis_handoff" in plan
    assert [agent["name"] for agent in plan["agents"]] == ["WRITER", "EDITOR"]
    assert plan["protocol"]["ir_path"] == "spec/ir.json"
    assert plan["protocol"]["state_machine_path"] == "spec/states.json"
    assert plan["runtime_monitor"]["required"] is True
    assert "one Dockerized CityOS app per agent" in (
        plan["cityos_synthesis_handoff"]["synthesizer_should_create"]
    )
    assert "Docker containers" in plan["cityos_synthesis_handoff"]["tracefix_should_not_create"]
    assert not (workspace / "Dockerfile").exists()


def test_export_cityos_module_plan_refuses_unverified_workspace(tmp_path):
    workspace = tmp_path / "workspace"
    (workspace / "spec").mkdir(parents=True)
    (workspace / "spec" / "ir.json").write_text(json.dumps({
        "agents": [{"id": "WRITER"}, {"id": "EDITOR"}],
        "resources": [],
        "channels": [
            {
                "id": "writer_to_editor",
                "from": "WRITER",
                "to": "EDITOR",
                "labels": ["submit"],
            },
        ],
    }))
    (workspace / "spec" / "Protocol.tla").write_text("---- MODULE Protocol ----\n")
    (workspace / "spec" / "summary.json").write_text(json.dumps({"tlc_passed": False}))

    with pytest.raises(ValueError, match="before successful protocol verification"):
        export_cityos_module_plan(workspace)

    assert not (workspace / "spec" / "cityos_module_plan.json").exists()
