import asyncio
import json
import tempfile
from pathlib import Path

from tracefix.runtime.cityos_agent_harness import CityOSAgentHarness, CityOSHarnessConfig
from tracefix.runtime.cityos_synthesizer import _agent_toml, _app_py

with tempfile.TemporaryDirectory() as d:
    root = Path(d)
    bundle = root / "tracefix_bundle"
    workspace = bundle / "workspace"
    prompt = workspace / "prompts" / "runtime_b" / "WRITER.md"
    prompt.parent.mkdir(parents=True)
    prompt.write_text("# writer\n", encoding="utf-8")
    (bundle / "plan.json").write_text(json.dumps({
        "version": "0.1",
        "application": {"name": "demo_cityos_app"},
    }), encoding="utf-8")
    (bundle / "agent.json").write_text(json.dumps({
        "name": "WRITER",
        "prompt_path": "prompts/runtime_b/WRITER.md",
    }), encoding="utf-8")
    cfg = CityOSHarnessConfig(
        "agent",
        "WRITER",
        bundle,
        False,
        "openai/gpt-4.1",
        ["opencode"],
        42.0,
        "http://tracefix-monitor:8780",
        "127.0.0.1",
        8780,
        "standalone",
        root / "out",
        root / "ready",
        False,
        8765,
        0.0,
        True,
        "",
    )
    harness = CityOSAgentHarness(cfg)
    assert harness.task_id == "demo_cityos_app"
    assert harness.prompt_path() == prompt
    payload = harness.readiness_payload()
    assert payload["agent"] == "WRITER"
    assert payload["prompt_path"] == str(prompt)
    ready_path = asyncio.run(harness.write_readiness())
    ready = json.loads(ready_path.read_text(encoding="utf-8"))
    assert ready["plan_version"] == "0.1"
    options = harness.orchestrator_options(agents=["WRITER"], coord_url=cfg.coord_url)
    assert options["agents"] == ["WRITER"]
    assert options["coord_url"] == cfg.coord_url
    assert options["output_dir"] == root / "out"
    app_source = _app_py("agent", "WRITER")
    toml = _agent_toml("demo-app", "WRITER")
    assert "CityOSAgentHarness.from_env" in app_source
    assert "tracefix.runtime.cli" not in app_source
    assert "TRACEFIX_COORD_URL=" in toml
    assert "TRACEFIX_OUTPUT_DIR=/app/demo-app/tracefix_output" in toml
print("cityos harness sanity ok")