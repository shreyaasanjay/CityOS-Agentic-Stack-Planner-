"""Tests for the Verified Pattern Repository."""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest

from tracefix.runtime.pattern_repository import (
    HarvestResult,
    NormalizedTopology,
    _candidate_id,
    _classify_shape,
    _suggest_pattern_name,
    harvest_candidate,
    list_candidates,
    normalize_topology,
)


# ---------------------------------------------------------------------------
# Fixtures: minimal verified workspace
# ---------------------------------------------------------------------------

def _make_workspace(tmp_path: Path, ir: dict, tlc_passed: bool = True) -> Path:
    """Create a minimal workspace with spec/ directory for harvesting."""
    ws = tmp_path / "workspace" / "test_run_20260701"
    spec = ws / "spec"
    spec.mkdir(parents=True)
    (spec / "ir.json").write_text(json.dumps(ir), encoding="utf-8")
    (spec / "Protocol.tla").write_text(
        "---- MODULE Protocol ----\n(* stub *)\n====\n", encoding="utf-8"
    )
    (spec / "Protocol.cfg").write_text("CONSTANTS A = A\n", encoding="utf-8")
    if tlc_passed:
        (spec / "summary.json").write_text(
            json.dumps({"tlc_passed": True}), encoding="utf-8"
        )
    return ws


_IR_TWO_AGENT = {
    "agents": [{"id": "worker"}, {"id": "verifier"}],
    "channels": [
        {"id": "w_to_v", "from": "worker", "to": "verifier"},
        {"id": "v_to_w", "from": "verifier", "to": "worker"},
    ],
    "resources": [{"id": "work_lock"}, {"id": "verify_lock"}],
}

_IR_CHAIN = {
    "agents": [{"id": "A"}, {"id": "B"}, {"id": "C"}],
    "channels": [
        {"id": "ab", "from": "A", "to": "B"},
        {"id": "bc", "from": "B", "to": "C"},
    ],
    "resources": [],
}

_IR_SINGLE = {
    "agents": [{"id": "task_agent"}],
    "channels": [],
    "resources": [],
}


# ---------------------------------------------------------------------------
# normalize_topology
# ---------------------------------------------------------------------------

def test_normalize_two_agent_handoff():
    ir = {
        "agents": [{"id": "scanner"}, {"id": "reporter"}],
        "channels": [{"id": "ch", "from": "scanner", "to": "reporter"}],
        "resources": [{"id": "scan_lock"}],
    }
    topo = normalize_topology(ir)
    assert topo.agent_count == 2
    assert topo.channel_count == 1
    assert topo.resource_count == 1
    assert topo.shape == "sequential_handoff"
    # Agents must be renamed to abstract labels
    assert set(topo.agents) == {"A", "B"}
    assert set(topo.resources) == {"R1"}
    assert len(topo.edges) == 1


def test_normalize_bidirectional_pair():
    topo = normalize_topology(_IR_TWO_AGENT)
    assert topo.agent_count == 2
    assert topo.channel_count == 2
    assert topo.shape == "bidirectional_pair"
    assert topo.resource_count == 2


def test_normalize_chain():
    topo = normalize_topology(_IR_CHAIN)
    assert topo.agent_count == 3
    assert topo.channel_count == 2
    assert topo.shape == "chain"
    assert topo.resource_count == 0


def test_normalize_single_agent():
    topo = normalize_topology(_IR_SINGLE)
    assert topo.agent_count == 1
    assert topo.shape == "single"
    assert topo.channel_count == 0


def test_normalize_empty():
    topo = normalize_topology({})
    assert topo.agent_count == 0
    assert topo.shape == "empty"


# ---------------------------------------------------------------------------
# Topology hash stability: name changes must not change the hash
# ---------------------------------------------------------------------------

def test_hash_stable_across_agent_name_change():
    """The same structural topology with different agent names must hash identically."""
    ir_a = {
        "agents": [{"id": "alpha"}, {"id": "beta"}],
        "channels": [{"id": "c1", "from": "alpha", "to": "beta"}],
        "resources": [],
    }
    ir_b = {
        "agents": [{"id": "scanner"}, {"id": "reporter"}],
        "channels": [{"id": "ch", "from": "scanner", "to": "reporter"}],
        "resources": [],
    }
    topo_a = normalize_topology(ir_a)
    topo_b = normalize_topology(ir_b)
    assert topo_a.topology_hash == topo_b.topology_hash


def test_hash_differs_for_different_topology():
    """Different structure must produce different hash."""
    ir_seq = {
        "agents": [{"id": "X"}, {"id": "Y"}],
        "channels": [{"id": "c", "from": "X", "to": "Y"}],
        "resources": [],
    }
    topo_seq = normalize_topology(ir_seq)
    topo_bidi = normalize_topology(_IR_TWO_AGENT)
    assert topo_seq.topology_hash != topo_bidi.topology_hash


def test_hash_stable_across_channel_order():
    """Channel declaration order must not affect the hash."""
    ir_1 = {
        "agents": [{"id": "P"}, {"id": "Q"}],
        "channels": [
            {"id": "a", "from": "P", "to": "Q"},
            {"id": "b", "from": "Q", "to": "P"},
        ],
        "resources": [],
    }
    ir_2 = {
        "agents": [{"id": "M"}, {"id": "N"}],
        "channels": [
            {"id": "x", "from": "N", "to": "M"},
            {"id": "y", "from": "M", "to": "N"},
        ],
        "resources": [],
    }
    assert normalize_topology(ir_1).topology_hash == normalize_topology(ir_2).topology_hash


# ---------------------------------------------------------------------------
# Shape classification
# ---------------------------------------------------------------------------

def test_shape_star():
    assert _classify_shape(["H", "S1", "S2", "S3"], [
        {"from": "H", "to": "S1"}, {"from": "H", "to": "S2"}, {"from": "H", "to": "S3"},
    ]) == "star"


def test_shape_ring():
    assert _classify_shape(["A", "B", "C"], [
        {"from": "A", "to": "B"}, {"from": "B", "to": "C"}, {"from": "C", "to": "A"},
    ]) == "ring"


def test_shape_isolated():
    assert _classify_shape(["A", "B"], []) == "isolated"


# ---------------------------------------------------------------------------
# Candidate creation
# ---------------------------------------------------------------------------

def test_harvest_creates_candidate(tmp_path, monkeypatch):
    ws = _make_workspace(tmp_path, _IR_TWO_AGENT)
    cand_root = tmp_path / "candidates"
    monkeypatch.setenv("TRACEFIX_PATTERN_REPOSITORY_ENABLED", "true")
    monkeypatch.setenv("TRACEFIX_PATTERN_REPOSITORY_DIR", str(cand_root))

    result = harvest_candidate(ws, task_text="worker submits to verifier")
    assert result.saved is True
    assert result.candidate_id
    assert result.topology_hash
    assert not result.deduplicated
    assert result.usage_count == 1

    # Check files on disk
    cand_dir = Path(result.candidate_path)
    assert (cand_dir / "candidate_metadata.json").exists()
    assert (cand_dir / "normalized_topology.json").exists()
    assert (cand_dir / "source_ir.json").exists()
    assert (cand_dir / "Protocol.tla").exists()
    assert (cand_dir / "README.md").exists()


def test_harvest_metadata_fields(tmp_path, monkeypatch):
    ws = _make_workspace(tmp_path, _IR_TWO_AGENT)
    cand_root = tmp_path / "candidates"
    monkeypatch.setenv("TRACEFIX_PATTERN_REPOSITORY_ENABLED", "true")
    monkeypatch.setenv("TRACEFIX_PATTERN_REPOSITORY_DIR", str(cand_root))

    result = harvest_candidate(
        ws,
        task_text="verify quorum before meeting",
        used_opencode_fallback=True,
        matched_existing_template=False,
    )
    assert result.saved
    meta = json.loads((Path(result.candidate_path) / "candidate_metadata.json").read_text())
    assert meta["tlc_passed"] is True
    assert meta["pluscal_passed"] is True
    assert meta["used_opencode_fallback"] is True
    assert meta["promotion_status"] == "candidate"
    assert meta["usage_count"] == 1
    assert "verify quorum" in meta["source_task_text"]


def test_harvest_candidate_not_marked_active_template(tmp_path, monkeypatch):
    ws = _make_workspace(tmp_path, _IR_TWO_AGENT)
    cand_root = tmp_path / "candidates"
    monkeypatch.setenv("TRACEFIX_PATTERN_REPOSITORY_DIR", str(cand_root))

    result = harvest_candidate(ws, task_text="test")
    meta = json.loads((Path(result.candidate_path) / "candidate_metadata.json").read_text())
    assert meta["promotion_status"] == "candidate"
    # Must NOT have a field like active=True or is_template=True
    assert "active" not in meta
    readme = (Path(result.candidate_path) / "README.md").read_text()
    assert "not an active protocol template" in readme


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------

def test_deduplication_increments_usage_count(tmp_path, monkeypatch):
    cand_root = tmp_path / "candidates"
    monkeypatch.setenv("TRACEFIX_PATTERN_REPOSITORY_ENABLED", "true")
    monkeypatch.setenv("TRACEFIX_PATTERN_REPOSITORY_DIR", str(cand_root))

    ws1 = _make_workspace(tmp_path / "ws1", _IR_TWO_AGENT)
    ws2 = _make_workspace(tmp_path / "ws2", _IR_TWO_AGENT)

    r1 = harvest_candidate(ws1, task_text="first run")
    assert r1.saved and not r1.deduplicated
    assert r1.usage_count == 1

    r2 = harvest_candidate(ws2, task_text="second run same structure")
    assert r2.saved and r2.deduplicated
    assert r2.usage_count == 2
    assert r1.candidate_id == r2.candidate_id

    meta = json.loads((Path(r2.candidate_path) / "candidate_metadata.json").read_text())
    assert meta["usage_count"] == 2
    assert len(meta["observed_workspaces"]) == 2


def test_deduplication_different_names_same_structure(tmp_path, monkeypatch):
    """Two runs with different agent IDs but identical topology must deduplicate."""
    cand_root = tmp_path / "candidates"
    monkeypatch.setenv("TRACEFIX_PATTERN_REPOSITORY_DIR", str(cand_root))

    ir_a = {
        "agents": [{"id": "alpha"}, {"id": "beta"}],
        "channels": [{"id": "c", "from": "alpha", "to": "beta"}],
        "resources": [],
    }
    ir_b = {
        "agents": [{"id": "scanner"}, {"id": "reporter"}],
        "channels": [{"id": "ch", "from": "scanner", "to": "reporter"}],
        "resources": [],
    }
    ws_a = _make_workspace(tmp_path / "ws_a", ir_a)
    ws_b = _make_workspace(tmp_path / "ws_b", ir_b)

    r1 = harvest_candidate(ws_a, task_text="alpha to beta")
    r2 = harvest_candidate(ws_b, task_text="scanner to reporter")
    assert r1.topology_hash == r2.topology_hash
    assert r2.deduplicated is True


# ---------------------------------------------------------------------------
# Single-agent fast path not harvested by default
# ---------------------------------------------------------------------------

def test_single_agent_not_harvested_by_default(tmp_path, monkeypatch):
    ws = _make_workspace(tmp_path, _IR_SINGLE)
    cand_root = tmp_path / "candidates"
    monkeypatch.setenv("TRACEFIX_PATTERN_REPOSITORY_DIR", str(cand_root))
    monkeypatch.setenv("TRACEFIX_HARVEST_SINGLE_AGENT", "false")

    result = harvest_candidate(ws, task_text="simple task", is_single_agent=True)
    assert result.saved is False
    assert "TRACEFIX_HARVEST_SINGLE_AGENT" in result.skip_reason


def test_single_agent_harvested_when_flag_set(tmp_path, monkeypatch):
    ws = _make_workspace(tmp_path, _IR_SINGLE)
    cand_root = tmp_path / "candidates"
    monkeypatch.setenv("TRACEFIX_PATTERN_REPOSITORY_DIR", str(cand_root))
    monkeypatch.setenv("TRACEFIX_HARVEST_SINGLE_AGENT", "true")

    result = harvest_candidate(ws, task_text="single task", is_single_agent=True)
    assert result.saved is True


# ---------------------------------------------------------------------------
# Repository disabled
# ---------------------------------------------------------------------------

def test_repository_disabled_skips_harvest(tmp_path, monkeypatch):
    ws = _make_workspace(tmp_path, _IR_TWO_AGENT)
    cand_root = tmp_path / "candidates"
    monkeypatch.setenv("TRACEFIX_PATTERN_REPOSITORY_DIR", str(cand_root))
    monkeypatch.setenv("TRACEFIX_PATTERN_REPOSITORY_ENABLED", "false")

    result = harvest_candidate(ws, task_text="test")
    assert result.saved is False
    assert "TRACEFIX_PATTERN_REPOSITORY_ENABLED=false" in result.skip_reason


# ---------------------------------------------------------------------------
# TLC not passed: no harvest
# ---------------------------------------------------------------------------

def test_no_harvest_when_tlc_not_passed(tmp_path, monkeypatch):
    ws = _make_workspace(tmp_path, _IR_TWO_AGENT, tlc_passed=False)
    cand_root = tmp_path / "candidates"
    monkeypatch.setenv("TRACEFIX_PATTERN_REPOSITORY_DIR", str(cand_root))

    result = harvest_candidate(ws, task_text="test")
    assert result.saved is False
    assert "tlc_passed" in result.skip_reason


# ---------------------------------------------------------------------------
# list_candidates
# ---------------------------------------------------------------------------

def test_list_candidates(tmp_path, monkeypatch):
    cand_root = tmp_path / "candidates"
    monkeypatch.setenv("TRACEFIX_PATTERN_REPOSITORY_DIR", str(cand_root))

    ws_a = _make_workspace(tmp_path / "ws_a", _IR_TWO_AGENT)
    ws_c = _make_workspace(tmp_path / "ws_c", _IR_CHAIN)

    harvest_candidate(ws_a, task_text="bidi pair task")
    harvest_candidate(ws_c, task_text="chain task")

    candidates = list_candidates(cand_root)
    assert len(candidates) == 2
    pattern_names = {c["suggested_pattern_name"] for c in candidates}
    assert "bidirectional_pair" in pattern_names
    assert "chain_3" in pattern_names


# ---------------------------------------------------------------------------
# Suggest pattern name
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("ir,expected_name", [
    (_IR_SINGLE, "single_agent"),
    (
        {
            "agents": [{"id": "a"}, {"id": "b"}],
            "channels": [{"id": "c", "from": "a", "to": "b"}],
            "resources": [],
        },
        "sequential_handoff",
    ),
    (_IR_TWO_AGENT, "bidirectional_pair"),
    (_IR_CHAIN, "chain_3"),
])
def test_suggest_pattern_name(ir, expected_name):
    topo = normalize_topology(ir)
    assert _suggest_pattern_name(topo) == expected_name
