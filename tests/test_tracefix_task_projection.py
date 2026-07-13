"""Focused tests for the compact TeLLMe-to-TraceFix handoff."""

from __future__ import annotations

import json
from pathlib import Path

from tracefix.runner_ui.tellme_bridge import TellMeBridge
from tellme_harness.query_analysis import analyze_query
from tellme_harness.route_policy import decide_route, infer_time_window
from tellme_harness.schemas import TellMeQuery
from tracefix.runtime.single_agent_fastpath import _extract_structured_task


def test_bridge_uses_compact_projection_but_keeps_full_tellme_artifacts(tmp_path: Path) -> None:
    bridge = TellMeBridge(tmp_path)
    result = bridge.process_query(
        query="How many people are currently in the conference room?",
        space_id="smart_room_1",
    )

    task_text = bridge.tracefix_task_text()
    compact = _extract_structured_task(task_text)
    assert compact is not None
    assert len(task_text) < 5000
    assert "evidence_card_contract" not in compact
    assert compact["capabilities"]["context_apis"]
    assert compact["privacy_constraints"]["raw_media"] == "disallowed"
    assert compact["output_contract"]["required_fields"]

    run_dir = Path(result["run_dir"])
    full_spec = json.loads((run_dir / "tracefix_task_spec.json").read_text(encoding="utf-8"))
    full_brief = json.loads((run_dir / "task_design_brief.json").read_text(encoding="utf-8"))
    assert "evidence_card_contract" in full_spec
    assert "evidence_card_requirements" in full_brief


def test_after_time_is_normalized_into_an_open_ended_window() -> None:
    window = infer_time_window(
        "Is it safe to leave the conference room lights on after 7 PM if someone is present?"
    )
    assert window is not None
    assert window.start == "19:00"
    assert window.end is None
    assert window.label == "after_time"


def test_named_identity_request_is_blocked() -> None:
    query = TellMeQuery(
        query_id="tellme_privacy_test",
        user_query="Identify which named person is currently in the conference room.",
        space_id="smart_room_1",
        created_at="2026-07-13T00:00:00+00:00",
    )
    decision = decide_route(query, analyze_query(query))
    assert decision.route == "not_allowed"
    assert "identity" in decision.rationale.lower()
