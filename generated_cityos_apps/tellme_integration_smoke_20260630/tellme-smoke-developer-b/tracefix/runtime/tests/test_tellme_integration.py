from pathlib import Path

from tracefix.runner_ui.server import _api_envelope
from tracefix.runner_ui.tellme_bridge import TellMeBridge


SAMPLE_QUERY = "Turn off the lights in the conference room after 7 PM unless someone is still present."


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
