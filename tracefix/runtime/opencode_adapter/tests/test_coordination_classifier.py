"""Tests for coordination_classifier and protocol template library."""
from __future__ import annotations

import pytest

from tracefix.runtime.coordination_classifier import (
    CoordinationPatternDecision,
    assess_coordination_pattern,
)
from tracefix.protocol_templates import (
    build_template,
    classify_all,
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
        "preprocessor then passes data to analyzer; analyzer processes results",
        tellme_spec={
            "agents": [
                {"id": "preprocessor", "role": "clean input data"},
                {"id": "analyzer", "role": "analyze cleaned data"},
            ]
        },
    )
    assert decision.pattern_id == "sequential_handoff"
    assert decision.confidence >= 0.75
    assert decision.ir_data
    assert decision.protocol_tla
    assert "---- MODULE Protocol ----" in decision.protocol_tla


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
    assert decision.pattern_id == "verifier_approver"
    assert decision.confidence >= 0.75
    assert len(decision.channels) == 2  # bidirectional


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
    assert decision.pattern_id == "producer_consumer"
    assert decision.confidence >= 0.75
    assert len(decision.resources) == 1  # only consumer lock


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
