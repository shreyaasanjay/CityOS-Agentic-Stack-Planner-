from tracefix.runner_ui.server import UsageState, _parse_usage_line, _workspace_has_no_llm_calls


def test_parse_stdout_token_summary():
    parsed = _parse_usage_line("  Total:  1,200 in + 345 out  ~$0.0123")

    assert parsed["input_tokens"] == 1200
    assert parsed["output_tokens"] == 345
    assert parsed["source"] == "stdout_usage_summary"


def test_usage_state_accumulates_and_prices_gpt41_mini():
    usage = UsageState(model="gpt-4.1-mini")

    assert usage.add_usage(input_tokens=100_000, output_tokens=10_000)
    snapshot = usage.snapshot()

    assert snapshot["input_tokens"] == 100_000
    assert snapshot["output_tokens"] == 10_000
    assert snapshot["total_tokens"] == 110_000
    assert snapshot["estimated_cost_usd"] > 0
    assert snapshot["estimated"] is True


def test_usage_state_merges_session_json_stats():
    usage = UsageState(model="gpt-4.1-mini")
    changed = usage.merge_session_stats({
        "config": {"model": "openai/gpt-4.1-mini"},
        "stats": {
            "prompt_tokens": 2000,
            "completion_tokens": 500,
            "estimated_cost_usd": 0.0016,
            "cost_known": True,
        },
    })

    snapshot = usage.snapshot()
    assert changed is True
    assert snapshot["source"] == "session_json"
    assert snapshot["input_tokens"] == 2000
    assert snapshot["output_tokens"] == 500
    assert snapshot["estimated"] is False


def test_usage_state_marks_deterministic_no_llm():
    usage = UsageState(model="glm-5.2")

    assert usage.mark_no_llm_calls() is True
    snapshot = usage.snapshot()
    assert snapshot["source"] == "deterministic_no_llm"
    assert snapshot["total_tokens"] == 0
    assert snapshot["cost_known"] is True
    assert snapshot["estimated_cost_usd"] == 0.0


def test_workspace_detects_no_llm_fast_path(tmp_path):
    output = tmp_path / "output"
    output.mkdir()
    (output / "pipeline_timing_report.json").write_text(
        '{"api_calls": [], "single_agent_fast_path": {"used": true}}',
        encoding="utf-8",
    )

    assert _workspace_has_no_llm_calls(tmp_path) is True

def test_usage_state_splits_mixed_model_usage():
    usage = UsageState()

    assert usage.add_usage(input_tokens=1000, output_tokens=200, model="gpt-4.1", component="TeLLMe")
    assert usage.add_usage(input_tokens=3000, output_tokens=700, model="glm-5.2", component="TraceFix")
    snapshot = usage.snapshot()

    assert snapshot["model"] == "Mixed models"
    assert snapshot["input_tokens"] == 4000
    assert snapshot["output_tokens"] == 900
    assert snapshot["total_tokens"] == 4900
    labels = {(row["component"], row["model"]): row["total_tokens"] for row in snapshot["model_breakdown"]}
    assert labels[("TeLLMe", "gpt-4.1")] == 1200
    assert labels[("TraceFix", "glm-5.2")] == 3700


def test_parse_json_usage_line_keeps_component():
    parsed = _parse_usage_line('{"component":"TeLLMe","model":"gpt-4.1","input_tokens":10,"output_tokens":5}')

    assert parsed["component"] == "TeLLMe"
    assert parsed["model"] == "gpt-4.1"
    assert parsed["input_tokens"] == 10
    assert parsed["output_tokens"] == 5