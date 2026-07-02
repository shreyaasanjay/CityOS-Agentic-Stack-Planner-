from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from tracefix.pipeline_timing import PipelineTimingReport, utc_now
from tracefix.repair_progress import (
    RepairConfig,
    RepairProgressTracker,
    meaningful_protocol_hash,
)


T0 = datetime(2026, 7, 1, tzinfo=timezone.utc)


def _baseline_failure(tracker: RepairProgressTracker, protocol: str = "A := 1;"):
    context = tracker.begin(protocol, now=T0)
    return tracker.finish(
        context,
        success=False,
        error_category="pcal_error",
        error_text="Expected ';' at line 10",
        progress_level=1,
        verification_duration_ms=100,
        now=T0 + timedelta(seconds=1),
    )


def test_defaults_keep_ten_attempts(monkeypatch, tmp_path):
    for name in (
        "TRACEFIX_MAX_REPAIR_ATTEMPTS",
        "TRACEFIX_REPEATED_ERROR_LIMIT",
        "TRACEFIX_NO_CHANGE_LIMIT",
        "TRACEFIX_REPAIR_TIME_BUDGET_SECONDS",
    ):
        monkeypatch.delenv(name, raising=False)
    tracker = RepairProgressTracker(tmp_path)
    assert tracker.config == RepairConfig(10, 2, 2, 600.0)


def test_environment_overrides_repair_limits(monkeypatch):
    monkeypatch.setenv("TRACEFIX_MAX_REPAIR_ATTEMPTS", "7")
    monkeypatch.setenv("TRACEFIX_REPEATED_ERROR_LIMIT", "3")
    monkeypatch.setenv("TRACEFIX_NO_CHANGE_LIMIT", "4")
    monkeypatch.setenv("TRACEFIX_REPAIR_TIME_BUDGET_SECONDS", "90")
    assert RepairConfig.from_environment() == RepairConfig(7, 3, 4, 90.0)


def test_two_unchanged_repairs_stop_before_another_verification(tmp_path):
    tracker = RepairProgressTracker(tmp_path)
    _baseline_failure(tracker)

    first = tracker.begin("A := 1;", now=T0 + timedelta(seconds=2))
    assert not first.blocked
    tracker.finish(
        first,
        success=False,
        error_category="pcal_error",
        error_text="Expected ';' at line 11",
        progress_level=1,
        verification_duration_ms=50,
        now=T0 + timedelta(seconds=3),
    )

    second = tracker.begin("A := 1;", now=T0 + timedelta(seconds=4))
    assert second.blocked
    assert second.stop_reason in {
        "no_meaningful_protocol_change",
        "protocol_hash_unchanged",
    }
    assert len(tracker.state["attempts"]) == 2


def test_changed_error_and_protocol_continue(tmp_path):
    tracker = RepairProgressTracker(tmp_path)
    _baseline_failure(tracker)
    context = tracker.begin("A := 2;", now=T0 + timedelta(seconds=2))
    decision = tracker.finish(
        context,
        success=False,
        error_category="deadlock",
        error_text="Deadlock reached at state 42",
        progress_level=2,
        verification_duration_ms=50,
        now=T0 + timedelta(seconds=3),
    )
    assert not decision.stop
    assert decision.attempt["protocol_changed"]
    assert decision.attempt["error_changed"]
    assert decision.attempt["verification_progressed"]


def test_time_budget_stops_waiting_repair(tmp_path):
    tracker = RepairProgressTracker(
        tmp_path,
        config=RepairConfig(time_budget_seconds=5),
    )
    _baseline_failure(tracker)
    context = tracker.begin("A := 2;", now=T0 + timedelta(seconds=10))
    assert context.blocked
    assert context.stop_reason == "repair_time_budget_exceeded"


def test_tlc_pass_stops_immediately_as_success(tmp_path):
    tracker = RepairProgressTracker(tmp_path)
    _baseline_failure(tracker)
    context = tracker.begin("A := 2;", now=T0 + timedelta(seconds=2))
    decision = tracker.finish(
        context,
        success=True,
        progress_level=3,
        verification_duration_ms=50,
        now=T0 + timedelta(seconds=3),
    )
    assert decision.stop
    assert decision.stop_reason == "tlc_passed"
    assert decision.attempt["progress_detected"]


def test_configured_maximum_stops_after_last_failed_repair(tmp_path):
    tracker = RepairProgressTracker(tmp_path, config=RepairConfig(max_attempts=1))
    _baseline_failure(tracker)
    context = tracker.begin("A := 2;", now=T0 + timedelta(seconds=2))
    decision = tracker.finish(
        context,
        success=False,
        error_category="deadlock",
        error_text="Deadlock reached",
        progress_level=2,
        verification_duration_ms=50,
        now=T0 + timedelta(seconds=3),
    )
    assert decision.stop
    assert decision.stop_reason == "max_repair_attempts_reached"


def test_meaningful_hash_ignores_comments_and_whitespace():
    left = "A := 1; \\* explanation\n"
    right = " A:=1;\n"
    assert meaningful_protocol_hash(left) == meaningful_protocol_hash(right)


def test_timing_report_includes_repair_attempts(tmp_path):
    spec = tmp_path / "spec"
    spec.mkdir()
    tracker = RepairProgressTracker(spec)
    _baseline_failure(tracker)
    context = tracker.begin("A := 2;", now=T0 + timedelta(seconds=2))
    tracker.finish(
        context,
        success=False,
        error_category="deadlock",
        error_text="Deadlock reached",
        progress_level=2,
        verification_duration_ms=50,
        now=T0 + timedelta(seconds=3),
    )

    report = PipelineTimingReport(tmp_path, run_kind="test")
    report.stage(
        "test",
        started_at=utc_now(),
        finished_at=utc_now(),
        duration_ms=1,
        success=True,
    )
    report.finalize()
    payload = json.loads(report.json_path.read_text(encoding="utf-8"))
    assert payload["repair"]["total_attempts"] == 1
    assert payload["repair"]["attempts"][0]["error_category"] == "deadlock"
