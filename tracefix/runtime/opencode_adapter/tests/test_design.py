"""Tests for `tracefix design` (headless opencode + skill) — no opencode spawned."""

from __future__ import annotations

import json
from pathlib import Path

from tracefix.runtime.opencode_adapter.config_gen import build_design_config
from tracefix.runtime.opencode_adapter.design import (
    _channel_diagnostics,
    _ensure_plan_before_prompts,
    _normalize_legacy_endeither_syntax,
    _runtime_prompts_current,
    _run_tlc_and_extract,
    _scaffold_valid_ir,
    build_designer_prompt,
    classify_design_artifacts,
    design_kickoff,
    ir_repair_kickoff,
    judge,
    pluscal_completion_kickoff,
    prompt_generation_kickoff,
    repo_root,
    slugify,
    validate_design_ir,
)


# --- naming -----------------------------------------------------------------

def test_slugify_short_and_safe():
    assert slugify("Design a 2PC protocol with a coordinator!") == \
        "design_a_2pc_protocol_with"
    assert slugify("___") == "design"


# --- designer prompt (skill injection) ---------------------------------------

def test_designer_prompt_embeds_skill_without_frontmatter():
    prompt = build_designer_prompt(repo_root())
    assert "Headless mode" in prompt                      # preamble first
    assert "name: tla-verify-pluscal" not in prompt        # frontmatter stripped
    assert "Phase 1: Structured Analysis" in prompt        # the actual workflow
    assert ".claude/skills/tla-prompt-gen/SKILL.md" in prompt  # Phase-5 redirection
    assert "Do NOT emit top-level or nested" in prompt
    assert "`locks`, `counters`" in prompt


def test_designer_prompt_preserves_literal_ir_json_examples():
    prompt = build_designer_prompt(repo_root())
    assert '{"id": "RESOURCE_ID", "type": "Lock"}' in prompt
    assert '{"initial": N}' in prompt
    assert "{prompt_gen_skill}" not in prompt


def test_kickoff_names_the_workspace():
    k = design_kickoff("workspace/my_task")
    assert "workspace/my_task/description.md" in k
    assert "prompts/runtime_b/" in k
    assert "`channels` must be non-empty" in k
    assert "Do not write `locks`" in k
    assert "Do not add arbitrary complete-graph channels" in k


def test_ir_repair_kickoff_requires_channel_rationale():
    k = ir_repair_kickoff("workspace/my_task", ["channels must not be empty"])
    assert "channels must be non-empty" in k
    assert "spec/ir_repair_notes.md" in k
    assert "Do not add `locks`" in k
    assert "Do not add arbitrary complete-graph channels" in k


def test_pluscal_completion_kickoff_continues_after_scaffold():
    k = pluscal_completion_kickoff("workspace/my_task")
    assert "Protocol.tla" in k
    assert "Run `tla-verify-pluscal verify`" in k
    assert "prompts/runtime_b/" in k
    assert "Do not redesign the IR" in k
    assert "Never write `endeither`" in k
    assert "spec/cityos_module_plan.json" in k
    assert "do not generate prompts from raw IR alone" in k


def test_prompt_generation_kickoff_requires_verified_plan():
    k = prompt_generation_kickoff("workspace/my_task")
    assert "spec/cityos_module_plan.json" in k
    assert "Protocol_translated.tla" in k
    assert "prompts/runtime_b/" in k
    assert "never from raw IR alone" in k
    assert "Do not edit `spec/ir.json`" in k


def test_normalize_legacy_endeither_syntax():
    tla = "\n".join([
        "choice:",
        "  either",
        "    \\* ok",
        "    { assert msg = \"ok\"; goto done; }",
        "  or",
        "    { assert msg = \"fail\";",
        "      goto retry; }",
        "  endeither",
        "done:",
        "  skip;",
    ])

    fixed, diagnostics = _normalize_legacy_endeither_syntax(tla)
    assert "endeither" not in fixed
    assert "  either {" in fixed
    assert "  } or {" in fixed
    assert "  };" in fixed
    assert "assert msg = \"ok\"; goto done;" in fixed
    assert "goto retry;" in fixed
    assert diagnostics


def test_prompt_gate_removes_prompts_created_before_plan(tmp_path):
    ws = tmp_path / "ws"
    spec = ws / "spec"
    pdir = ws / "prompts" / "runtime_b"
    spec.mkdir(parents=True)
    pdir.mkdir(parents=True)
    (spec / "Protocol.tla").write_text("---- MODULE Protocol ----\n====\n")
    (spec / "Protocol_translated.tla").write_text("---- MODULE Protocol ----\n====\n")
    (spec / "states.json").write_text("{}")
    (spec / "summary.json").write_text(json.dumps({"tlc_passed": True}))
    prompt = pdir / "A.md"
    prompt.write_text("# stale")
    plan = spec / "cityos_module_plan.json"
    plan.write_text("{}")
    old = plan.stat().st_mtime - 10
    import os
    os.utime(prompt, (old, old))

    diagnostics = _ensure_plan_before_prompts(ws)

    assert any("removed 1 stale runtime prompt" in item for item in diagnostics)
    assert not prompt.exists()
    assert not _runtime_prompts_current(ws)


# --- design config (no MCP, headless-safe permissions) ------------------------

def test_design_config_shape():
    cfg = build_design_config("THE PROMPT", model="openai/gpt-5.4")
    assert "mcp" not in cfg                                # designer has no coord layer
    assert set(cfg["agent"]) == {"designer", "designer_ir_repair"}
    agent = cfg["agent"]["designer"]
    assert agent["prompt"] == "THE PROMPT"
    assert agent["model"] == "openai/gpt-5.4"
    perm = agent["permission"]
    assert perm["bash"] == "allow" and perm["edit"] == "allow"
    assert perm["task"] == "deny" and perm["question"] == "deny"
    repair = cfg["agent"]["designer_ir_repair"]
    assert repair["model"] == "openai/gpt-5.4"
    assert "IR repair mode" in repair["prompt"]
    assert "Do not add locks, counters" in repair["prompt"]
    assert repair["permission"] == perm


def test_design_config_omits_model_when_unset():
    cfg = build_design_config("p")
    assert "model" not in cfg["agent"]["designer"]
    assert "model" not in cfg["agent"]["designer_ir_repair"]


# --- artifact judging ---------------------------------------------------------

def _make_ws(tmp_path: Path, *, tlc_passed=True, states=True,
             prompts=("A", "B"), agents=("A", "B"), repairs=1,
             protocol=True) -> Path:
    ws = tmp_path / "ws"
    spec = ws / "spec"
    spec.mkdir(parents=True)
    (spec / "ir.json").write_text(json.dumps(
        {"agents": [{"id": a} for a in agents], "resources": [], "channels": []}))
    if protocol:
        (spec / "Protocol.tla").write_text("---- MODULE Protocol ----\n====\n")
    if states:
        (spec / "states.json").write_text("{}")
    (spec / "summary.json").write_text(json.dumps(
        {"tlc_passed": tlc_passed, "total_repairs": repairs}))
    pdir = ws / "prompts" / "runtime_b"
    pdir.mkdir(parents=True)
    for p in prompts:
        (pdir / f"{p}.md").write_text(f"# {p}")
    return ws


def test_judge_ready(tmp_path):
    r = judge(_make_ws(tmp_path))
    assert r.success and r.status == "ready"
    assert r.agents == ["A", "B"] and r.prompts == ["A", "B"] and r.repairs == 1


def test_judge_verify_failed_is_honest(tmp_path):
    r = judge(_make_ws(tmp_path, tlc_passed=False))
    assert not r.success and r.status == "tlc_error"


def test_judge_missing_prompt_for_an_agent_is_incomplete(tmp_path):
    r = judge(_make_ws(tmp_path, prompts=("A",)))   # agent B has no prompt
    assert not r.success and r.status == "incomplete"


def test_judge_missing_states_is_incomplete(tmp_path):
    r = judge(_make_ws(tmp_path, states=False))
    assert not r.success and r.status == "incomplete"


def test_judge_timeout_label(tmp_path):
    r = judge(_make_ws(tmp_path, tlc_passed=None, states=False, prompts=()),
              timed_out=True)
    assert r.status == "timeout"


def test_judge_empty_workspace(tmp_path):
    ws = tmp_path / "empty"
    ws.mkdir()
    r = judge(ws)
    assert not r.success and r.status == "ir_incomplete" and r.agents == []


def test_validate_design_ir_normalizes_string_agents(tmp_path):
    ws = tmp_path / "ws"
    spec = ws / "spec"
    spec.mkdir(parents=True)
    (spec / "ir.json").write_text(json.dumps({
        "agents": ["A", "B"],
        "resources": [{"id": "R", "type": "Lock"}],
        "channels": [{"id": "a_to_b", "from": "A", "to": "B", "labels": ["go"]}],
    }))

    valid, errors, diagnostics = validate_design_ir(ws)
    assert valid, errors
    assert "IR validation passed" in diagnostics
    normalized = json.loads((spec / "ir.json").read_text())
    assert normalized["agents"] == [{"id": "A"}, {"id": "B"}]


def test_validate_design_ir_sanitizes_agent_description(tmp_path):
    ws = tmp_path / "ws"
    spec = ws / "spec"
    spec.mkdir(parents=True)
    (spec / "ir.json").write_text(json.dumps({
        "agents": [{"id": "A", "description": "worker"}, {"id": "B"}],
        "resources": [{"id": "R", "type": "Lock"}],
        "channels": [{"id": "a_to_b", "from": "A", "to": "B", "labels": ["go"]}],
    }))
    report = {}

    valid, errors, diagnostics = validate_design_ir(
        ws,
        sanitization_report=report,
    )

    assert valid, errors
    assert report["removed_fields"] == ["$.agents[0].description"]
    assert report["validation_before"]["valid"] is False
    assert report["validation_after"]["valid"] is True
    assert report["recovered"] is True
    assert any("removed $.agents[0].description" in item for item in diagnostics)
    sanitized = json.loads((spec / "ir.json").read_text())
    assert sanitized["agents"] == [{"id": "A"}, {"id": "B"}]


def test_validate_design_ir_canonicalizes_agent_role(tmp_path):
    ws = tmp_path / "ws"
    spec = ws / "spec"
    spec.mkdir(parents=True)
    (spec / "ir.json").write_text(json.dumps({
        "agents": [
            {"id": "A", "role": "produce evidence"},
            {"id": "B", "role": "verify evidence"},
        ],
        "resources": [],
        "channels": [
            {"id": "a_to_b", "from": "A", "to": "B", "labels": ["submit"]},
        ],
    }))
    report = {}

    valid, errors, diagnostics = validate_design_ir(
        ws, sanitization_report=report
    )

    persisted = json.loads((spec / "ir.json").read_text())
    assert valid, errors
    assert all("role" not in agent for agent in persisted["agents"])
    assert report["removed_fields"] == ["$.agents[0].role", "$.agents[1].role"]
    assert any("passed after sanitization" in item for item in diagnostics)


def test_canonicalizer_does_not_hide_unknown_structural_field(tmp_path):
    ws = tmp_path / "ws"
    spec = ws / "spec"
    spec.mkdir(parents=True)
    (spec / "ir.json").write_text(json.dumps({
        "agents": [{"id": "A", "agent_id": "wrong-contract-field"}],
        "resources": [],
        "channels": [],
    }))

    valid, errors, _ = validate_design_ir(ws)

    assert valid is False
    assert any("$.agents[0].agent_id" in error for error in errors)


def test_validate_design_ir_sanitizer_preserves_structural_failure(tmp_path):
    ws = tmp_path / "ws"
    spec = ws / "spec"
    spec.mkdir(parents=True)
    original = {
        "agents": [{"id": "A", "notes": "harmless"}, {"id": "B"}],
        "resources": [],
        "channels": [],
    }
    (spec / "ir.json").write_text(json.dumps(original))
    report = {}

    valid, errors, _ = validate_design_ir(ws, sanitization_report=report)

    assert not valid
    assert any("no communication channels" in error for error in errors)
    assert report["validation_after"]["valid"] is False
    assert report["recovered"] is False
    assert json.loads((spec / "ir.json").read_text()) == original


def test_validate_design_ir_sanitizer_never_removes_required_fields(tmp_path):
    ws = tmp_path / "ws"
    spec = ws / "spec"
    spec.mkdir(parents=True)
    (spec / "ir.json").write_text(json.dumps({
        "agents": [{"description": "missing required id"}],
        "resources": [],
        "channels": [],
    }))
    report = {}

    valid, errors, _ = validate_design_ir(ws, sanitization_report=report)

    assert not valid
    assert any("'id' is a required property" in error for error in errors)
    assert report["removed_fields"] == ["$.agents[0].description"]
    assert report["validation_after"]["valid"] is False


def test_sanitized_ir_still_uses_normal_scaffold_gate(tmp_path):
    ws = tmp_path / "ws"
    spec = ws / "spec"
    spec.mkdir(parents=True)
    (spec / "ir.json").write_text(json.dumps({
        "agents": [{"id": "A", "rationale": "sender"}, {"id": "B"}],
        "resources": [{"id": "R", "type": "Lock"}],
        "channels": [{
            "id": "a_to_b",
            "from": "A",
            "to": "B",
            "labels": ["work ready"],
            "comments": "handoff",
        }],
    }))

    valid, errors, _ = validate_design_ir(ws)
    assert valid, errors
    diagnostics = _scaffold_valid_ir(ws)

    assert "Scaffold fallback wrote Protocol.tla and Protocol.cfg" in diagnostics
    sanitized = json.loads((spec / "ir.json").read_text())
    assert sanitized["channels"][0]["labels"] == ["work_ready"]
    assert "comments" not in sanitized["channels"][0]
    assert (spec / "Protocol.tla").is_file()
    assert (spec / "Protocol.cfg").is_file()


def test_timing_ignores_missing_optional_opencode_disposition(tmp_path):
    from tracefix.pipeline_timing import PipelineTimingReport

    report = PipelineTimingReport(tmp_path, run_kind="test")
    report.opencode_call("opencode_optional", None)

    assert report.api_calls == []
    assert report.stages[-1]["stage"] == "opencode_optional"
    assert report.stages[-1]["success"] is True
    assert report.stages[-1]["skipped"] is True


def test_simple_prompt_uses_single_agent_fast_path():
    from tracefix.runtime.single_agent_fastpath import assess_single_agent_fast_path

    decision = assess_single_agent_fast_path(
        "How many people are in the room right now?"
    )

    assert decision.eligible is True
    assert decision.agent_id == "OCCUPANCY_ANALYZER"


def test_coordination_prompt_falls_back_to_opencode():
    from tracefix.runtime.single_agent_fastpath import assess_single_agent_fast_path

    decision = assess_single_agent_fast_path(
        "Coordinate three agents to validate authorization and produce an audit report."
    )

    assert decision.eligible is False
    assert "coordination signal" in decision.reason


def test_multi_source_prompt_falls_back_to_opencode():
    from tracefix.runtime.single_agent_fastpath import assess_single_agent_fast_path

    decision = assess_single_agent_fast_path(
        "Verify the room count against badge logs and meeting attendance."
    )

    assert decision.eligible is False
    assert "multi-source signal" in decision.reason


def test_fast_path_ir_passes_strict_validation():
    from tracefix.pipeline.pipeline.validator import validate_ir
    from tracefix.runtime.single_agent_fastpath import (
        assess_single_agent_fast_path,
        generate_single_agent_ir,
    )

    decision = assess_single_agent_fast_path("Summarize this log file.")
    ir = generate_single_agent_ir(decision)
    result = validate_ir(ir)

    assert result.valid, result.errors
    assert ir["agents"] == [{"id": "SUMMARY_AGENT"}]
    assert ir["channels"] == []


def test_structured_tellme_occupancy_uses_fast_path():
    from tracefix.runtime.single_agent_fastpath import assess_single_agent_fast_path

    task = """TeLLMe structured smart-room application requirements.
Structured task specification:
{
  "user_query": "How many people are in the room right now?",
  "route": "single_agent",
  "required_modalities": ["video"],
  "candidate_harnesses": [
    "occupancy_context_harness",
    "answer_synthesis_harness"
  ],
  "evidence_plan": {
    "primary_evidence": ["occupancy_context_harness_packet"],
    "supporting_evidence": [],
    "conflicting_evidence_checks": []
  },
  "application_goal": {"goal_type": "occupancy_count"}
}
"""
    decision = assess_single_agent_fast_path(task)

    assert decision.eligible is True
    assert decision.structured_input is True
    assert decision.task_text == "How many people are in the room right now?"


def test_fast_path_runtime_prompt_requires_verified_plan():
    import pytest
    from tracefix.runtime.single_agent_fastpath import (
        assess_single_agent_fast_path,
        render_verified_runtime_prompt,
    )

    decision = assess_single_agent_fast_path("Summarize this log file.")

    with pytest.raises(ValueError, match="production-ready"):
        render_verified_runtime_prompt(
            decision,
            {"verification": {"production_ready": False}},
        )


def test_fast_path_runtime_prompt_derives_from_verified_plan():
    from tracefix.runtime.single_agent_fastpath import (
        assess_single_agent_fast_path,
        render_verified_runtime_prompt,
    )

    decision = assess_single_agent_fast_path("Summarize this log file.")
    prompt = render_verified_runtime_prompt(decision, {
        "verification": {"production_ready": True},
        "agents": [{"name": "SUMMARY_AGENT"}],
        "protocol": {
            "allowed_transitions": [{
                "agent": "SUMMARY_AGENT",
                "from": "SUMMARY_AGENT_start",
                "to": "SUMMARY_AGENT_done",
            }],
        },
    })

    assert "Summarize this log file." in prompt
    assert "`SUMMARY_AGENT_start` -> `SUMMARY_AGENT_done`" in prompt
    assert "after PlusCal/TLC verification" in prompt


def test_validate_design_ir_normalizes_legacy_locks_and_counters(tmp_path):
    ws = tmp_path / "ws"
    spec = ws / "spec"
    spec.mkdir(parents=True)
    (spec / "ir.json").write_text(json.dumps({
        "agents": ["A", "B"],
        "resources": [],
        "locks": ["SHARED_FILE"],
        "counters": [{"id": "API_POOL", "initial": 2}],
        "channels": [{"id": "a_to_b", "from": "A", "to": "B", "labels": ["go"]}],
    }))

    valid, errors, diagnostics = validate_design_ir(ws)
    assert valid, errors
    assert any("$.locks[0]" in item for item in diagnostics)
    assert any("$.counters[0]" in item for item in diagnostics)
    normalized = json.loads((spec / "ir.json").read_text())
    assert "locks" not in normalized
    assert "counters" not in normalized
    assert {"id": "SHARED_FILE", "type": "Lock"} in normalized["resources"]
    assert {"id": "API_POOL", "type": "Counter", "config": {"initial": 2}} in normalized["resources"]


def test_validate_design_ir_rejects_nested_locks_with_path(tmp_path):
    ws = tmp_path / "ws"
    spec = ws / "spec"
    spec.mkdir(parents=True)
    (spec / "ir.json").write_text(json.dumps({
        "agents": [{"id": "A", "locks": ["SHARED_FILE"]}, {"id": "B"}],
        "resources": [{"id": "SHARED_FILE", "type": "Lock"}],
        "channels": [{"id": "a_to_b", "from": "A", "to": "B", "labels": ["go"]}],
    }))

    valid, errors, diagnostics = validate_design_ir(ws)
    assert not valid
    assert any("$.agents[0].locks" in error for error in errors)
    assert "IR validation failed" in diagnostics


def test_scaffold_valid_ir_writes_protocol_artifacts(tmp_path):
    ws = tmp_path / "ws"
    spec = ws / "spec"
    spec.mkdir(parents=True)
    (spec / "ir.json").write_text(json.dumps({
        "agents": [{"id": "A"}, {"id": "B"}],
        "resources": [{"id": "R", "type": "Lock"}],
        "channels": [{"id": "a_to_b", "from": "A", "to": "B", "labels": ["go"]}],
    }))

    diagnostics = _scaffold_valid_ir(ws)

    assert "Scaffold fallback wrote Protocol.tla and Protocol.cfg" in diagnostics
    assert (spec / "Protocol.tla").exists()
    assert (spec / "Protocol.cfg").exists()


def test_classify_design_artifacts_ir_incomplete_for_empty_channels(tmp_path):
    ws = tmp_path / "ws"
    spec = ws / "spec"
    spec.mkdir(parents=True)
    (spec / "ir.json").write_text(json.dumps({
        "agents": [{"id": "A"}, {"id": "B"}],
        "resources": [{"id": "R", "type": "Lock"}],
        "channels": [],
    }))

    status, errors, diagnostics = classify_design_artifacts(ws)
    assert status == "ir_incomplete"
    assert any("no communication channels" in error for error in errors)
    assert "TLC did not run" in diagnostics


def test_classify_design_artifacts_tlc_error(tmp_path):
    ws = tmp_path / "ws"
    spec = ws / "spec"
    spec.mkdir(parents=True)
    (spec / "ir.json").write_text(json.dumps({
        "agents": [{"id": "A"}, {"id": "B"}],
        "resources": [{"id": "R", "type": "Lock"}],
        "channels": [{"id": "a_to_b", "from": "A", "to": "B", "labels": ["go"]}],
    }))
    (spec / "Protocol.tla").write_text("---- MODULE Protocol ----")
    (spec / "tlc_error.md").write_text("# TLC failed")

    status, errors, diagnostics = classify_design_artifacts(ws)
    assert status == "tlc_error"
    assert errors == []
    assert "TLC error artifact present" in diagnostics


def test_classify_design_artifacts_pluscal_error_includes_artifact_inventory(tmp_path):
    ws = tmp_path / "ws"
    spec = ws / "spec"
    spec.mkdir(parents=True)
    (spec / "ir.json").write_text(json.dumps({
        "agents": [{"id": "A"}, {"id": "B"}],
        "resources": [{"id": "R", "type": "Lock"}],
        "channels": [{"id": "a_to_b", "from": "A", "to": "B", "labels": ["go"]}],
    }))
    (spec / "Protocol.tla").write_text("---- MODULE Protocol ----\n====\n")
    # No states.json, no tlc_error.md — the pluscal_error case.

    status, errors, diagnostics = classify_design_artifacts(ws)

    assert status == "pluscal_error"
    assert any("states.json is missing" in d for d in diagnostics)
    # The diagnostic should mention Protocol.cfg and tlc_output.log presence.
    inv_diag = next(d for d in diagnostics if "states.json is missing" in d)
    assert "Protocol.cfg=" in inv_diag
    assert "tlc_output.log=" in inv_diag


def test_run_tlc_and_extract_writes_tlc_error_when_cfg_missing(tmp_path):
    ws = tmp_path / "ws"
    spec = ws / "spec"
    spec.mkdir(parents=True)
    (spec / "ir.json").write_text(json.dumps({
        "agents": [{"id": "A"}, {"id": "B"}],
        "resources": [],
        "channels": [{"id": "a_to_b", "from": "A", "to": "B", "labels": ["go"]}],
    }))
    # Protocol.tla present but Protocol.cfg absent
    (spec / "Protocol.tla").write_text("---- MODULE Protocol ----\n====\n")

    diagnostics = _run_tlc_and_extract(ws)

    assert any("Protocol.cfg missing" in d for d in diagnostics)
    assert (spec / "tlc_error.md").exists()
    assert "Protocol.cfg missing" in (spec / "tlc_error.md").read_text()


def test_channel_diagnostics_reports_added_channels():
    before = {"channels": []}
    after = {
        "channels": [
            {
                "id": "a_to_b",
                "from": "A",
                "to": "B",
                "labels": ["handoff", "review"],
            }
        ]
    }

    diagnostics = _channel_diagnostics(before, after)
    assert diagnostics == [
        "IR repair added channel a_to_b: A -> B labels=[handoff, review]"
    ]
