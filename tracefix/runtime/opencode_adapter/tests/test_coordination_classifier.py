"""Tests for coordination_classifier and protocol template library."""
from __future__ import annotations

import json

import pytest

from tracefix.runtime.coordination_classifier import (
    CoordinationPatternDecision,
    assess_coordination_pattern,
)
from tracefix.runtime.template_adaptation_repair import (
    adapt_template_decision,
    validate_adapted_ir_policy,
)
from tracefix.protocol_templates import (
    build_template,
    classify_all,
    get_template_metadata,
    list_pattern_ids,
)
from tracefix.pipeline.pipeline.validator import (
    canonicalize_ir_with_diagnostics,
    validate_canonical_ir,
    validate_ir,
)


THREE_SOURCE_PROMPT = (
    "Make a readiness decision by comparing observed room occupancy, expected "
    "attendance records, and badge check-in status. Reconcile conflicting evidence "
    "and identify unresolved issues before the final decision."
)

FIVE_SOURCE_PROMPT = (
    "Determine readiness by independently evaluating observed room occupancy, "
    "expected attendance records, badge check-in status, calendar participation "
    "updates, and equipment readiness. Reconcile conflicting evidence and generate "
    "a final readiness decision."
)

TRAFFIC_PROMPT = (
    "Design a Smart City traffic management system for a four-way intersection. "
    "Coordinate traffic flow safely, prevent conflicting green lights, prioritize "
    "emergency vehicles, and enter all-red state on failure."
)


# ---------------------------------------------------------------------------
# Registry smoke tests
# ---------------------------------------------------------------------------

def test_list_pattern_ids_non_empty():
    ids = list_pattern_ids()
    assert len(ids) >= 3
    assert "sequential_handoff" in ids
    assert "verifier_approver" in ids
    assert "producer_consumer" in ids
    assert "fan_in_decision" in ids
    assert "traffic_signal_coordination" in ids


def test_four_way_traffic_selects_deterministic_template():
    decision = assess_coordination_pattern(TRAFFIC_PROMPT)

    assert decision.considered is True
    assert decision.pattern_id == "traffic_signal_coordination"
    assert decision.confidence > 0.95
    assert decision.template_variant == "four_way_emergency"
    assert decision.app_agent_count == 6
    assert len(decision.channels) == 9
    assert len(decision.resources) == 2
    assert "emergency_detector" in {agent["id"] for agent in decision.agents}
    assert validate_ir(decision.ir_data).valid
    assert "---- MODULE Protocol ----" in decision.protocol_tla
    assert "NoConflictingGreens" in decision.protocol_tla
    assert "AllRedOnCompletion" in decision.protocol_tla


def test_traffic_template_emits_displayable_channels_and_locks():
    decision = assess_coordination_pattern(TRAFFIC_PROMPT)
    channels = {channel["id"]: channel for channel in decision.channels}
    resources = {resource["id"]: resource for resource in decision.resources}

    assert "north_approach_to_signal_controller" in channels
    assert "signal_controller_to_north_approach" in channels
    assert "emergency_detected" in channels[
        "emergency_detector_to_signal_controller"
    ]["labels"]
    assert "grant_green" in channels[
        "signal_controller_to_north_approach"
    ]["labels"]
    assert "all_red" in channels[
        "signal_controller_to_north_approach"
    ]["labels"]
    assert resources == {
        "intersection_green_lock": {
            "id": "intersection_green_lock",
            "type": "Lock",
        },
        "emergency_override_lock": {
            "id": "emergency_override_lock",
            "type": "Lock",
        },
    }
    assert decision.ir_data["agent_resources"]["signal_controller"] == [
        "intersection_green_lock",
        "emergency_override_lock",
    ]
    assert "NoOrphanLocks" in decision.protocol_tla


def test_multi_agent_traffic_scores_raw_and_structured_context():
    decision = assess_coordination_pattern(
        "Coordinate this application safely.",
        tellme_spec={
            "route": "multi_agent",
            "user_query": "Manage a four-way traffic intersection.",
            "candidate_harnesses": [
                "TRAFFIC_SIGNAL_CONTROLLER",
                "EMERGENCY_VEHICLE_PRIORITY",
                "ALL_RED_FAILURE_HARNESS",
            ],
            "application_goals": [
                "Prevent conflicting green lights",
            ],
        },
    )

    assert decision.considered is True
    assert decision.pattern_id == "traffic_signal_coordination"


def test_registry_template_ir_is_canonical_and_valid():
    ir, _ = build_template("sequential_handoff", {
        "agent_a_id": "producer",
        "agent_b_id": "consumer",
        "agent_a_role": "produce evidence",
        "agent_b_role": "consume evidence",
    })

    assert all("role" not in agent for agent in ir["agents"])
    assert validate_ir(ir).valid


def test_three_sources_select_fan_in_decision():
    decision = assess_coordination_pattern(THREE_SOURCE_PROMPT)

    assert decision.pattern_id == "fan_in_decision"
    assert decision.evidence_source_count == 3
    assert decision.app_agent_count == 4
    assert decision.monitor_count == 1


def test_five_sources_select_fan_in_decision():
    decision = assess_coordination_pattern(FIVE_SOURCE_PROMPT)

    assert decision.pattern_id == "fan_in_decision"
    assert decision.evidence_source_count == 5
    assert decision.app_agent_count == 6


def test_fan_in_template_creates_n_sources_and_channels():
    decision = assess_coordination_pattern(FIVE_SOURCE_PROMPT)
    decision_id = decision.decision_agent_id

    evidence_agents = [
        agent for agent in decision.agents if agent["id"] != decision_id
    ]
    assert len(evidence_agents) == 5
    assert len(decision.channels) == 5
    assert {channel["from"] for channel in decision.channels} == {
        agent["id"] for agent in evidence_agents
    }
    assert {channel["to"] for channel in decision.channels} == {decision_id}
    assert all("monitor" not in agent["id"] for agent in decision.agents)


def test_fan_in_ir_is_canonical_and_strictly_valid():
    decision = assess_coordination_pattern(THREE_SOURCE_PROMPT)
    canonical, report = canonicalize_ir_with_diagnostics(decision.ir_data)

    assert canonical == decision.ir_data
    assert report["changed"] is False
    assert validate_canonical_ir(canonical).valid


# ---------------------------------------------------------------------------
# Classifier: fail-closed behavior
# ---------------------------------------------------------------------------

def test_no_match_returns_none():
    decision = assess_coordination_pattern("do something with three agents simultaneously")
    assert decision.pattern_id is None


def test_single_agent_returns_none():
    decision = assess_coordination_pattern("agent processes a batch of records")
    assert decision.pattern_id is None
    assert decision.considered is False


def test_uncertain_task_returns_none():
    """Generic 2-agent description with no keyword signals → fail-closed."""
    decision = assess_coordination_pattern(
        "agent_a and agent_b work together on a task",
        tellme_spec={"agents": [{"id": "agent_a"}, {"id": "agent_b"}]},
    )
    assert decision.pattern_id is None


# ---------------------------------------------------------------------------
# Sequential handoff
# ---------------------------------------------------------------------------

def test_sequential_handoff_match():
    decision = assess_coordination_pattern(
        "preprocessor completes input cleanup, then hands off cleaned data to analyzer for downstream processing",
        tellme_spec={
            "agents": [
                {"id": "preprocessor", "role": "clean input data"},
                {"id": "analyzer", "role": "analyze cleaned data"},
            ]
        },
    )
    assert decision.pattern_id is None
    assert decision.template_match_type == "none"
    assert decision.all_scores[0][0] == "sequential_handoff"
    assert decision.all_scores[0][1] <= 0.95
    assert "falling back to OpenCode" in decision.fallback_reason


def test_sequential_handoff_template_direct():
    ir, tla = build_template("sequential_handoff", {
        "agent_a_id": "scanner",
        "agent_b_id": "reporter",
    })
    assert len(ir["agents"]) == 2
    assert ir["agents"][0]["id"] == "scanner"
    assert len(ir["channels"]) == 1
    assert "scanner_to_reporter" in tla
    assert "Reporter" in tla or "reporter" in tla
    assert "acquire_lock" in tla
    assert "AllDone" in tla
    assert "NoOrphanLocks" in tla
    assert "ChannelsDrained" in tla
    assert "ChannelBound" in tla


def test_sequential_handoff_negative_keywords_suppress():
    """'verify' keyword should suppress sequential_handoff confidence."""
    scores = classify_all(
        "agent_a verifies and approves work from agent_b",
        agent_count_hint=2,
        keywords=frozenset(),
    )
    sh_score = next((s for pid, s in scores if pid == "sequential_handoff"), 0.0)
    assert sh_score == 0.0


# ---------------------------------------------------------------------------
# Verifier / approver
# ---------------------------------------------------------------------------

def test_verifier_approver_match():
    decision = assess_coordination_pattern(
        "worker submits code for review; reviewer approves or rejects the submission",
        tellme_spec={
            "agents": [
                {"id": "worker", "role": "write code"},
                {"id": "reviewer", "role": "approve or reject code"},
            ]
        },
    )
    assert decision.pattern_id is None
    assert decision.template_match_type == "none"
    assert decision.all_scores[0][0] == "verifier_approver"
    assert decision.all_scores[0][1] <= 0.95
    assert "falling back to OpenCode" in decision.fallback_reason


def test_verifier_approver_template_direct():
    ir, tla = build_template("verifier_approver", {
        "worker_id": "submitter",
        "verifier_id": "checker",
    })
    assert len(ir["agents"]) == 2
    assert len(ir["resources"]) == 2
    assert len(ir["channels"]) == 2
    ch_ids = {ch["id"] for ch in ir["channels"]}
    assert "submitter_to_checker" in ch_ids
    assert "checker_to_submitter" in ch_ids
    # Verify-or-reject branch
    assert "either" in tla
    assert '"approved"' in tla
    assert '"rejected"' in tla
    assert "NoOrphanLocks" in tla
    assert "ChannelsDrained" in tla


# ---------------------------------------------------------------------------
# Producer / consumer
# ---------------------------------------------------------------------------

def test_producer_consumer_match():
    decision = assess_coordination_pattern(
        "producer generates items and consumer processes each item",
        tellme_spec={
            "agents": [
                {"id": "producer", "role": "generate data items"},
                {"id": "consumer", "role": "process data items"},
            ]
        },
    )
    assert decision.pattern_id is None
    assert decision.template_match_type == "none"
    assert decision.all_scores[0][0] == "producer_consumer"
    assert decision.all_scores[0][1] <= 0.95
    assert "falling back to OpenCode" in decision.fallback_reason


def test_producer_consumer_template_direct():
    ir, tla = build_template("producer_consumer", {
        "producer_id": "generator",
        "consumer_id": "processor",
    })
    assert len(ir["agents"]) == 2
    assert len(ir["resources"]) == 1  # consumer only
    assert ir["resources"][0]["id"] == "processor_output"
    assert len(ir["channels"]) == 1
    assert "generator_to_processor" in tla
    assert "acquire_lock" in tla
    assert "AllDone" in tla


def test_producer_consumer_no_lock_for_producer():
    """Producer process must NOT call acquire_lock."""
    _, tla = build_template("producer_consumer", {
        "producer_id": "gen",
        "consumer_id": "proc",
    })
    # The producer section ends before the consumer section
    proc_index = tla.index("gen_proc")
    cons_index = tla.index("proc_proc")
    producer_section = tla[proc_index:cons_index]
    assert "acquire_lock" not in producer_section


# ---------------------------------------------------------------------------
# Template instantiation: IR structure invariants
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("pattern_id,params", [
    ("sequential_handoff", {"agent_a_id": "a", "agent_b_id": "b"}),
    ("verifier_approver", {"worker_id": "w", "verifier_id": "v"}),
    ("producer_consumer", {"producer_id": "p", "consumer_id": "c"}),
])
def test_ir_structure_valid(pattern_id, params):
    ir, tla = build_template(pattern_id, params)
    assert isinstance(ir["agents"], list)
    assert len(ir["agents"]) == 2
    for agent in ir["agents"]:
        assert "id" in agent
    assert isinstance(ir.get("resources", []), list)
    assert isinstance(ir.get("channels", []), list)
    assert "---- MODULE Protocol ----" in tla
    assert "(* --algorithm Protocol {" in tla
    assert "} *)" in tla
    assert "AllDone" in tla
    assert "TypeInvariant" in tla
    assert "NoOrphanLocks" in tla
    assert "ChannelsDrained" in tla
    assert "ChannelBound" in tla


# ---------------------------------------------------------------------------
# decision dataclass completeness
# ---------------------------------------------------------------------------

def test_decision_dataclass_fields():
    d = CoordinationPatternDecision()
    assert d.considered is False
    assert d.pattern_id is None
    assert d.confidence == 0.0
    assert d.ir_data == {}
    assert d.protocol_tla == ""
    assert isinstance(d.all_scores, list)


def test_traffic_template_metadata_declares_parameterized_shape():
    metadata = get_template_metadata("traffic_signal_coordination")

    assert metadata["shape"] == "parameterized"
    assert "standard_four_way" in metadata["supported_variants"]
    assert "n_approach" in metadata["supported_variants"]
    assert "approach_ids or approach_count" in metadata["required_inputs"]


def test_standard_traffic_without_emergency_is_five_agent_shape():
    prompt = (
        "Design a four-way traffic signal controller. Coordinate traffic flow "
        "safely, prevent conflicting green lights, and enter all-red on failure."
    )
    decision = assess_coordination_pattern(prompt)

    assert decision.pattern_id == "traffic_signal_coordination"
    assert decision.template_variant == "standard_four_way"
    assert {agent["id"] for agent in decision.agents} == {
        "north_approach",
        "east_approach",
        "south_approach",
        "west_approach",
        "signal_controller",
    }
    assert len(decision.channels) == 8
    assert [resource["id"] for resource in decision.resources] == ["intersection_green_lock"]
    assert validate_ir(decision.ir_data).valid


def test_emergency_traffic_variant_adds_detector_channel_and_override_lock():
    decision = assess_coordination_pattern(TRAFFIC_PROMPT)
    channels = {channel["id"]: channel for channel in decision.channels}
    resources = {resource["id"] for resource in decision.resources}

    assert decision.template_variant == "four_way_emergency"
    assert "emergency_detector" in {agent["id"] for agent in decision.agents}
    assert channels["emergency_detector_to_signal_controller"]["labels"] == [
        "emergency_detected",
        "emergency_cleared",
    ]
    assert "emergency_override_lock" in resources


def test_pedestrian_traffic_variant_adds_crossing_agent_channels_and_lock():
    decision = assess_coordination_pattern(
        "Design a four-way traffic signal with pedestrian crossings, safe "
        "vehicle phases, and all-red failure handling."
    )
    channels = {channel["id"]: channel for channel in decision.channels}
    resources = {resource["id"] for resource in decision.resources}

    assert decision.pattern_id == "traffic_signal_coordination"
    assert decision.template_variant == "four_way_pedestrian"
    assert "pedestrian_crossing_agent" in {agent["id"] for agent in decision.agents}
    assert "pedestrian_crossing_agent_to_signal_controller" in channels
    assert "signal_controller_to_pedestrian_crossing_agent" in channels
    assert "pedestrian_phase_lock" in resources
    assert validate_ir(decision.ir_data).valid


def test_five_approach_traffic_variant_has_deterministic_displayable_channels():
    decision = assess_coordination_pattern(
        "Design a five-approach traffic intersection signal controller that "
        "coordinates traffic safely, prevents conflicting green lights, and "
        "enters all-red on failure."
    )
    channels = {channel["id"] for channel in decision.channels}

    assert decision.pattern_id == "traffic_signal_coordination"
    assert decision.template_variant == "n_approach"
    assert len(decision.agents) == 6
    assert len(decision.channels) == 10
    assert "approach_5_to_signal_controller" in channels
    assert "signal_controller_to_approach_5" in channels
    assert validate_ir(decision.ir_data).valid


def test_unsupported_traffic_network_falls_back_to_opencode():
    decision = assess_coordination_pattern(
        "Design a citywide traffic network across multiple intersections and "
        "corridors with adaptive routing."
    )

    assert decision.considered is True
    assert decision.pattern_id is None
    assert "falling back to OpenCode" in decision.fallback_reason

# ---------------------------------------------------------------------------
# Three protocol-generation scenarios and bounded template adaptation
# ---------------------------------------------------------------------------

def test_traffic_prompt_is_parameterized_template_and_skips_opencode_family():
    decision = assess_coordination_pattern(TRAFFIC_PROMPT)

    assert decision.pattern_id == "traffic_signal_coordination"
    assert decision.template_match_type == "parameterized"
    assert decision.confidence > 0.95
    assert validate_ir(decision.ir_data).valid


def test_partial_traffic_match_gets_bounded_status_exchange_adaptation():
    prompt = (
        "Design a traffic coordination system with five agents where the "
        "controller must communicate with each approach, and east and west "
        "approaches must also exchange status messages before the controller "
        "grants green."
    )
    decision = assess_coordination_pattern(prompt)

    assert decision.pattern_id == "traffic_signal_coordination"
    assert decision.template_match_type == "partial"
    assert 0.50 <= decision.confidence <= 0.95

    adapted = adapt_template_decision(task=prompt, tellme_spec=None, decision=decision)

    assert adapted.accepted is True
    assert adapted.repair_stage == "template_adaptation_repair"
    assert adapted.llm_used is False
    assert "channels" in adapted.adapted_fields
    channel_ids = {channel["id"] for channel in adapted.ir_data["channels"]}
    assert "east_approach_to_west_approach_status_exchange" in channel_ids
    assert "west_approach_to_east_approach_status_exchange" in channel_ids
    assert validate_ir(adapted.ir_data).valid
    assert "east_approach_to_west_approach_status_exchange" in adapted.protocol_tla
    assert "west_approach_to_east_approach_status_exchange" in adapted.protocol_tla


def test_no_template_match_falls_back_to_opencode_generation():
    prompt = (
        "Design a collaborative drone swarm that negotiates three dimensional "
        "airspace corridors with weather-aware battery swapping and auctioned "
        "charging reservations."
    )
    decision = assess_coordination_pattern(prompt)

    assert decision.pattern_id is None
    assert decision.template_match_type == "none"
    assert decision.fallback_reason




def test_partial_traffic_prompts_report_distinct_requested_changes():
    prompts = [
        (
            "Design a traffic controller where east and west approaches exchange "
            "status messages before the controller grants green.",
            "status_exchange",
        ),
        (
            "Design a traffic controller where east and west approaches share "
            "congestion status and queue length before green is granted.",
            "queue_length",
        ),
        (
            "Design a traffic controller where east and west approaches communicate "
            "emergency clearance status before green is granted.",
            "emergency_clearance",
        ),
    ]
    seen_metadata = []
    for prompt, expected_label in prompts:
        decision = assess_coordination_pattern(
            prompt,
            tellme_spec={"route": "multi_agent", "user_query": prompt},
        )
        assert decision.pattern_id == "traffic_signal_coordination"
        assert decision.template_match_type == "partial"
        adapted = adapt_template_decision(task=prompt, tellme_spec=None, decision=decision)
        assert adapted.accepted is True
        labels = adapted.applied_changes[0]["labels"]
        assert expected_label in labels
        assert adapted.requested_changes
        assert adapted.applied_changes
        assert validate_ir(adapted.ir_data).valid
        seen_metadata.append(json.dumps({
            "requested": adapted.requested_changes,
            "applied": adapted.applied_changes,
            "summary": adapted.repair_summary,
        }, sort_keys=True))

    assert len(set(seen_metadata)) == len(prompts)


def test_opencode_fallback_has_no_deterministic_template_lane():
    prompt = (
        "Design a collaborative drone swarm that negotiates three dimensional "
        "airspace corridors with weather-aware battery swapping and auctioned "
        "charging reservations."
    )
    decision = assess_coordination_pattern(prompt)

    assert decision.pattern_id is None
    assert decision.template_match_type == "none"
    assert decision.fallback_reason


def test_adaptation_policy_rejects_removed_base_agent():
    decision = assess_coordination_pattern(TRAFFIC_PROMPT)
    adapted_ir = dict(decision.ir_data)
    adapted_ir["agents"] = [
        agent for agent in decision.ir_data["agents"]
        if agent["id"] != "signal_controller"
    ]

    errors = validate_adapted_ir_policy(
        decision.ir_data,
        adapted_ir,
        decision.template_metadata,
    )

    assert any("removed required agents" in error for error in errors)


def test_adaptation_policy_rejects_disconnected_extra_channel():
    decision = assess_coordination_pattern(TRAFFIC_PROMPT)
    adapted_ir = json.loads(json.dumps(decision.ir_data))
    adapted_ir["channels"].append({
        "id": "ghost_to_controller",
        "from": "ghost_agent",
        "to": "signal_controller",
        "labels": ["status_exchange"],
    })

    errors = validate_adapted_ir_policy(
        decision.ir_data,
        adapted_ir,
        decision.template_metadata,
    )

    assert any("disconnected endpoint" in error for error in errors)


def test_adaptation_policy_allows_safe_channel_addition():
    decision = assess_coordination_pattern(TRAFFIC_PROMPT)
    adapted_ir = json.loads(json.dumps(decision.ir_data))
    adapted_ir["channels"].append({
        "id": "north_approach_to_east_approach_status",
        "from": "north_approach",
        "to": "east_approach",
        "labels": ["status_exchange"],
    })

    errors = validate_adapted_ir_policy(
        decision.ir_data,
        adapted_ir,
        decision.template_metadata,
    )

    assert errors == []

