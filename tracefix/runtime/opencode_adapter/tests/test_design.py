"""Tests for `tracefix design` (headless opencode + skill) — no opencode spawned."""

from __future__ import annotations

import json
from pathlib import Path

from tracefix.runtime.opencode_adapter.config_gen import build_design_config
from tracefix.runtime.opencode_adapter.design import (
    _channel_diagnostics,
    build_designer_prompt,
    classify_design_artifacts,
    design_kickoff,
    ir_repair_kickoff,
    judge,
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


def test_kickoff_names_the_workspace():
    k = design_kickoff("workspace/my_task")
    assert "workspace/my_task/description.md" in k
    assert "prompts/runtime_b/" in k
    assert "`channels` must be non-empty" in k
    assert "Do not add arbitrary complete-graph channels" in k


def test_ir_repair_kickoff_requires_channel_rationale():
    k = ir_repair_kickoff("workspace/my_task", ["channels must not be empty"])
    assert "channels must be non-empty" in k
    assert "spec/ir_repair_notes.md" in k
    assert "Do not add arbitrary complete-graph channels" in k


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
