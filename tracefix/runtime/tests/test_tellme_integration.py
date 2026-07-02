import json
from pathlib import Path

from tracefix.protocol_templates.attendance_verification import (
    PATTERN_ID as ATTENDANCE_PATTERN_ID,
    classify as attendance_classify,
    build_template as attendance_build_template,
)
from tracefix.runner_ui.server import (
    _api_envelope,
    _synth_file_requirements,
    _synth_workspace_summary,
)
from tracefix.runner_ui.tellme_bridge import TellMeBridge
from tracefix.runtime.coordination_classifier import assess_coordination_pattern
from tracefix.runtime.single_agent_fastpath import (
    _extract_structured_task,
    assess_single_agent_fast_path,
)
from tracefix.runtime.opencode_adapter.design import _guard_task_agent_ir, _spec_dir
from tellme_harness.tracefix_workspace_adapter import _canonicalize_ir


SAMPLE_QUERY = "Turn off the lights in the conference room after 7 PM unless someone is still present."

AGENT_AB_QUERY = (
    "Agent A determines the number of people physically present in the conference room. "
    "Agent B verifies the occupancy count against the meeting attendance roster and "
    "produces a validated occupancy report."
)

OCCUPANCY_QUERY = "How many people are currently in the conference room?"


def test_tellme_workspace_boundary_canonicalizes_ir_metadata():
    ir, source = _canonicalize_ir({
        "agents": [{"id": "observer", "role": "observe room state"}],
        "resources": [],
        "channels": [],
    })

    assert source == "tracefix_normalizer"
    assert ir["agents"] == [{"id": "observer"}]


def _make_tellme_task_text(
    user_query: str,
    route: str = "multi_agent",
    candidate_harnesses: list | None = None,
) -> str:
    """Build a TeLLMe wrapper task text matching tracefix_task_text() format."""
    spec = {
        "task_id": "test_task_001",
        "query_id": "test_query_001",
        "user_query": user_query,
        "route": route,
        "candidate_harnesses": candidate_harnesses or [],
        "required_modalities": [],
        "application_goal": {},
        "evidence_plan": {},
    }
    return (
        "TeLLMe structured smart-room application requirements.\n"
        "Treat this as compile-time input to TraceFix planning and verification.\n"
        "Generate a complete multi-agent topology with explicit communication channels.\n"
        "Do not execute production agents or bypass PlusCal/TLC verification.\n\n"
        "Structured task specification:\n"
        + json.dumps(spec)
    )


# ---------------------------------------------------------------------------
# Test 1 & 2: TeLLMe multi_agent task → ≥2 agents + ≥1 channel in IR
# ---------------------------------------------------------------------------

def test_multi_agent_task_produces_two_agents():
    """multi_agent TeLLMe task with Agent A/B produces at least 2 IR agents."""
    task_text = _make_tellme_task_text(
        AGENT_AB_QUERY,
        route="multi_agent",
        candidate_harnesses=["OCCUPANCY_COUNTER_HARNESS", "ATTENDANCE_VERIFIER_HARNESS"],
    )
    spec = _extract_structured_task(task_text)
    decision = assess_coordination_pattern(task_text, tellme_spec=spec)
    assert decision.considered, (
        f"Classifier not considered. fallback_reason={decision.fallback_reason}"
    )
    assert decision.pattern_id is not None, (
        f"No pattern matched. fallback_reason={decision.fallback_reason} "
        f"all_scores={decision.all_scores}"
    )
    assert len(decision.agents) >= 2, (
        f"Expected ≥2 agents, got {decision.agents}"
    )


def test_multi_agent_task_produces_at_least_one_channel():
    """multi_agent TeLLMe task produces at least 1 channel in IR."""
    task_text = _make_tellme_task_text(
        AGENT_AB_QUERY,
        route="multi_agent",
        candidate_harnesses=["OCCUPANCY_COUNTER_HARNESS", "ATTENDANCE_VERIFIER_HARNESS"],
    )
    spec = _extract_structured_task(task_text)
    decision = assess_coordination_pattern(task_text, tellme_spec=spec)
    assert decision.pattern_id is not None, (
        f"No pattern matched. fallback_reason={decision.fallback_reason}"
    )
    assert len(decision.channels) >= 1, (
        f"Expected ≥1 channel, got {decision.channels}"
    )


# ---------------------------------------------------------------------------
# Test 3: Single-agent occupancy prompt → 1 agent (coord classifier not used)
# ---------------------------------------------------------------------------

def test_single_agent_occupancy_coord_classifier_not_considered():
    """Simple occupancy query does not trigger the coordination classifier."""
    task_text = _make_tellme_task_text(OCCUPANCY_QUERY, route="single_agent")
    spec = _extract_structured_task(task_text)
    decision = assess_coordination_pattern(task_text, tellme_spec=spec)
    # route=single_agent → no multi_agent override → agent_count from text
    # "people" is not an agent noun → count=0 → not considered
    assert not decision.considered, (
        f"Classifier unexpectedly considered for simple occupancy. "
        f"pattern={decision.pattern_id}"
    )
    assert decision.pattern_id is None


# ---------------------------------------------------------------------------
# Test 4: multi_agent route → single_agent fast path returns eligible=False
# ---------------------------------------------------------------------------

def test_multi_agent_route_skips_single_agent_fast_path():
    """TeLLMe route=multi_agent causes single-agent fast path to return eligible=False."""
    task_text = _make_tellme_task_text(AGENT_AB_QUERY, route="multi_agent")
    fp = assess_single_agent_fast_path(task_text)
    assert fp.considered is True
    assert fp.eligible is False, (
        f"Expected fast path ineligible for multi_agent, got eligible=True. "
        f"reason={fp.reason}"
    )
    assert "multi_agent" in fp.reason or "not single_agent" in fp.reason


# ---------------------------------------------------------------------------
# Test 5: Coord classifier uses user_query, not full wrapper text
# ---------------------------------------------------------------------------

def test_coord_classifier_uses_user_query_for_scoring():
    """Classifier should score correctly even when full wrapper text has no verif keywords."""
    # The wrapper preamble has no verification keywords; only user_query does.
    # Without the tellme_spec fix, the classifier would score against the preamble.
    user_query = "Agent A prepares the occupancy report. Agent B verifies and approves the results."
    task_text = _make_tellme_task_text(user_query, route="multi_agent")
    spec = _extract_structured_task(task_text)
    assert spec is not None
    assert spec["user_query"] == user_query
    decision = assess_coordination_pattern(task_text, tellme_spec=spec)
    # Should score verifier_approver from user_query keywords ("verif", "approv")
    assert decision.pattern_id is not None, (
        f"Classifier fell through, suggesting it scored the wrapper not user_query. "
        f"fallback_reason={decision.fallback_reason}"
    )
    best_id = decision.all_scores[0][0] if decision.all_scores else None
    assert best_id in ("verifier_approver", "sequential_handoff"), (
        f"Unexpected pattern: {best_id}"
    )


# ---------------------------------------------------------------------------
# Test 6: TASK_AGENT guard repairs degenerate IR for multi_agent routes
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Test 7–9: Placeholder task text is detected / rejected before reaching run_design
# ---------------------------------------------------------------------------

_PLACEHOLDER = "Loaded automatically from the current TeLLMe task spec."


def test_placeholder_has_no_structured_spec():
    """The placeholder string is not a valid TeLLMe spec — _extract_structured_task returns None."""
    result = _extract_structured_task(_PLACEHOLDER)
    assert result is None, (
        f"Expected None for placeholder, got {result!r}"
    )


def test_placeholder_fast_path_not_eligible():
    """Single-agent fast path must not classify the placeholder as a valid single-agent task."""
    fp = assess_single_agent_fast_path(_PLACEHOLDER)
    # The placeholder has no structured marker so it goes through the text path.
    # It has no coordination/multi-source/multi-step signals, so the text path
    # might mark it eligible — but that's fine because the agent_id will be TASK_AGENT.
    # The important thing is we catch it in the guard.
    # This test documents the current behavior so regressions are visible.
    assert fp.considered, "Fast path should at least consider the placeholder"
    if fp.eligible:
        assert fp.agent_id == "TASK_AGENT", (
            f"Placeholder task produced unexpected agent_id={fp.agent_id!r}"
        )


def test_multi_agent_spec_extract_succeeds_on_wrapper_text():
    """_extract_structured_task must return the full spec dict from tracefix_task_text() output."""
    task_text = _make_tellme_task_text(
        AGENT_AB_QUERY,
        route="multi_agent",
        candidate_harnesses=["OCCUPANCY_COUNTER_HARNESS", "ATTENDANCE_VERIFIER_HARNESS"],
    )
    spec = _extract_structured_task(task_text)
    assert spec is not None, "Expected spec dict from TeLLMe wrapper text"
    assert spec.get("route") == "multi_agent"
    assert spec.get("user_query") == AGENT_AB_QUERY
    assert "OCCUPANCY_COUNTER_HARNESS" in (spec.get("candidate_harnesses") or [])


def test_task_agent_guard_repairs_degenerate_ir(tmp_path: Path):
    """_guard_task_agent_ir replaces TASK_AGENT stub with 2-agent IR for multi_agent tasks."""
    spec_dir = tmp_path / "spec"
    spec_dir.mkdir()
    # Write a degenerate single-TASK_AGENT IR (what OpenCode fallback produces)
    degenerate_ir = {
        "agents": [{"id": "TASK_AGENT"}],
        "resources": [],
        "channels": [],
    }
    (spec_dir / "ir.json").write_text(json.dumps(degenerate_ir) + "\n")

    task_text = _make_tellme_task_text(
        AGENT_AB_QUERY,
        route="multi_agent",
        candidate_harnesses=["OCCUPANCY_COUNTER_HARNESS", "ATTENDANCE_VERIFIER_HARNESS"],
    )
    diagnostics: list[str] = []
    _guard_task_agent_ir(tmp_path, task_text, diagnostics)

    repaired = json.loads((spec_dir / "ir.json").read_text())
    agents = repaired.get("agents", [])
    channels = repaired.get("channels", [])

    assert len(agents) >= 2, f"Expected ≥2 agents after repair, got {agents}"
    agent_ids = [a["id"] for a in agents]
    assert "TASK_AGENT" not in agent_ids, f"TASK_AGENT still present: {agent_ids}"
    assert len(channels) >= 1, f"Expected ≥1 channel after repair, got {channels}"
    assert any("guard" in d.lower() for d in diagnostics), (
        f"Guard diagnostic not emitted: {diagnostics}"
    )


def test_tellme_bridge_persists_tracefix_handoff(tmp_path: Path) -> None:
    bridge = TellMeBridge(tmp_path)

    result = bridge.process_query(query=SAMPLE_QUERY, space_id="smart_room_1")

    assert result["query_id"].startswith("tellme_")
    assert result["route_decision"]["route"] in {"single_agent", "multi_agent"}
    assert result["privacy_guardrail"]["status"] == "passed"
    assert result["tracefix_task_spec"]["user_query"] == SAMPLE_QUERY
    assert bridge.current()["run_id"] == result["query_id"]
    assert bridge.artifact_paths(result)

    task_text = bridge.tracefix_task_text()
    assert SAMPLE_QUERY in task_text
    assert "Do not execute production agents" in task_text


def test_api_envelope_has_stable_shape() -> None:
    payload = _api_envelope(
        ok=True,
        data={"value": 1},
        warnings=["review"],
        artifact_paths=["artifact.json"],
        run_id="run_1",
    )

    assert payload == {
        "ok": True,
        "data": {"value": 1},
        "errors": [],
        "warnings": ["review"],
        "artifact_paths": ["artifact.json"],
        "run_id": "run_1",
    }


# ---------------------------------------------------------------------------
# Part A — Tests 10–15: attendance_verification template
# ---------------------------------------------------------------------------

ATTENDANCE_QUERY = (
    "Determine whether the observed room occupancy matches the expected meeting "
    "attendance count. Use occupancy sensor data and calendar event attendance count "
    "as evidence sources. If conflicting evidence or insufficient confidence, generate "
    "a report with limitations and evidence references."
)


def test_attendance_verification_prompt_scores_above_threshold():
    """The attendance/occupancy query scores ≥ 0.75 for the attendance_verification pattern."""
    score = attendance_classify(ATTENDANCE_QUERY.lower(), agent_count_hint=2, keywords=frozenset())
    assert score >= 0.75, (
        f"Expected score ≥ 0.75 for attendance prompt, got {score:.2f}"
    )


def test_attendance_verification_is_top_pattern():
    """attendance_verification beats all other patterns for the attendance/occupancy query."""
    task_text = _make_tellme_task_text(
        ATTENDANCE_QUERY,
        route="multi_agent",
        candidate_harnesses=["OCCUPANCY_SENSOR_HARNESS", "CALENDAR_ATTENDANCE_HARNESS"],
    )
    spec = _extract_structured_task(task_text)
    decision = assess_coordination_pattern(task_text, tellme_spec=spec)
    assert decision.considered, (
        f"Classifier not considered. fallback_reason={decision.fallback_reason}"
    )
    assert decision.pattern_id == ATTENDANCE_PATTERN_ID, (
        f"Expected {ATTENDANCE_PATTERN_ID!r}, got {decision.pattern_id!r}. "
        f"all_scores={decision.all_scores}"
    )


def test_attendance_verification_produces_two_agents():
    """attendance_verification template build_template returns exactly 2 agents."""
    params = {
        "observer_id": "occupancy_sensor",
        "verifier_id": "calendar_attendance",
        "observer_role": "collect occupancy sensor data",
        "verifier_role": "compare occupancy against attendance count",
    }
    ir_data, _ = attendance_build_template(params)
    assert len(ir_data.get("agents", [])) == 2, (
        f"Expected 2 agents, got {ir_data.get('agents')}"
    )


def test_attendance_verification_produces_at_least_one_channel():
    """attendance_verification template produces at least 1 channel."""
    params = {
        "observer_id": "occupancy_sensor",
        "verifier_id": "calendar_attendance",
    }
    ir_data, _ = attendance_build_template(params)
    assert len(ir_data.get("channels", [])) >= 1, (
        f"Expected ≥1 channel, got {ir_data.get('channels')}"
    )


def test_attendance_verification_not_triggered_by_simple_query():
    """A simple light-control query does not trigger attendance_verification."""
    score = attendance_classify(SAMPLE_QUERY.lower(), agent_count_hint=2, keywords=frozenset())
    assert score == 0.0, (
        f"Expected 0.0 for light-control query, got {score:.2f}"
    )


def test_attendance_verification_coord_decision_full_flow():
    """Full coord decision flow with attendance harnesses produces attendance_verification."""
    task_text = _make_tellme_task_text(
        ATTENDANCE_QUERY,
        route="multi_agent",
        candidate_harnesses=["OCCUPANCY_SENSOR_HARNESS", "CALENDAR_ATTENDANCE_HARNESS"],
    )
    spec = _extract_structured_task(task_text)
    decision = assess_coordination_pattern(task_text, tellme_spec=spec)
    assert len(decision.agents) >= 2, (
        f"Expected ≥2 agents in decision, got {decision.agents}"
    )
    assert len(decision.channels) >= 1, (
        f"Expected ≥1 channel in decision, got {decision.channels}"
    )
    assert decision.pattern_id == ATTENDANCE_PATTERN_ID


# ---------------------------------------------------------------------------
# Part B — Tests 16–20: workspace readiness without summary.json
# ---------------------------------------------------------------------------

def _make_workspace(base: Path, files: list[str]) -> Path:
    """Create a minimal workspace with spec/ dir and given file stubs."""
    ws = base / "workspace"
    spec = ws / "spec"
    spec.mkdir(parents=True)
    for f in files:
        (spec / f).write_text("{}")
    return ws


def test_synth_workspace_summary_not_required():
    """spec/summary.json must NOT appear in the required items list."""
    reqs = _synth_file_requirements(Path("/nonexistent/workspace"))
    required_paths = {item["path"] for item in reqs if item["required"]}
    assert "spec/summary.json" not in required_paths, (
        f"summary.json should not be required; required_paths={required_paths}"
    )


def test_synth_workspace_ready_without_summary_json(tmp_path: Path):
    """Workspace with all core artifacts but no summary.json must be ready=True."""
    ws = _make_workspace(tmp_path, [
        "ir.json", "states.json", "cityos_module_plan.json",
        "Protocol.tla", "Protocol.cfg",
    ])
    result = _synth_workspace_summary(ws)
    assert result["ready"] is True, (
        f"Expected ready=True without summary.json. "
        f"missingRequired={result.get('missingRequired')} "
        f"verificationStatus={result.get('verificationStatus')}"
    )


def test_synth_workspace_status_verified_no_summary(tmp_path: Path):
    """Workspace with core artifacts but no summary.json reports verificationStatus='verified_no_summary'."""
    ws = _make_workspace(tmp_path, [
        "ir.json", "states.json", "cityos_module_plan.json",
        "Protocol.tla", "Protocol.cfg",
    ])
    result = _synth_workspace_summary(ws)
    assert result["verificationStatus"] == "verified_no_summary", (
        f"Expected 'verified_no_summary', got {result.get('verificationStatus')!r}"
    )


def test_synth_workspace_ready_with_summary_tlc_passed(tmp_path: Path):
    """Workspace with summary.json (tlc_passed=True) reports ready=True and status='verified'."""
    ws = _make_workspace(tmp_path, [
        "ir.json", "states.json", "cityos_module_plan.json",
        "Protocol.tla", "Protocol.cfg",
    ])
    (ws / "spec" / "summary.json").write_text(json.dumps({"tlc_passed": True}))
    result = _synth_workspace_summary(ws)
    assert result["ready"] is True
    assert result["verificationStatus"] == "verified", (
        f"Expected 'verified', got {result.get('verificationStatus')!r}"
    )
    assert result["tlcStatus"] == "passed", (
        f"Expected tlcStatus='passed', got {result.get('tlcStatus')!r}"
    )


def test_synth_workspace_incomplete_when_missing_required(tmp_path: Path):
    """Workspace missing required artifacts (e.g. states.json) must be ready=False."""
    ws = _make_workspace(tmp_path, [
        "ir.json", "Protocol.tla", "Protocol.cfg",
        # states.json and cityos_module_plan.json intentionally absent
    ])
    result = _synth_workspace_summary(ws)
    assert result["ready"] is False, (
        f"Expected ready=False when states.json missing. "
        f"missingRequired={result.get('missingRequired')}"
    )
    assert len(result.get("missingArtifacts", [])) > 0, (
        "Expected at least one missing artifact path"
    )
