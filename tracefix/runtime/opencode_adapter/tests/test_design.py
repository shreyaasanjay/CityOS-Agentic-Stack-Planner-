"""Tests for `tracefix design` (headless opencode + skill) — no opencode spawned."""

from __future__ import annotations

import json
from pathlib import Path

from tracefix.runtime.opencode_adapter.config_gen import build_design_config
from tracefix.runtime.opencode_adapter.design import (
    _channel_diagnostics,
    _scaffold_valid_ir,
    build_designer_prompt,
    classify_design_artifacts,
    design_kickoff,
    ir_repair_kickoff,
    judge,
    pluscal_completion_kickoff,
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
             prompts=("A", "B"), agents=("A", "B"), repairs=1) -> Path:
    ws = tmp_path / "ws"
    spec = ws / "spec"
    spec.mkdir(parents=True)
    (spec / "ir.json").write_text(json.dumps(
        {"agents": [{"id": a} for a in agents], "resources": [], "channels": []}))
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
