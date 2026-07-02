from tracefix.runner_ui.server import UsageState, _parse_usage_line


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
