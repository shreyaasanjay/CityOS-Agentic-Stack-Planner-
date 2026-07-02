from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

from sdk.python.cityos import CityosServicer


BUNDLE_DIR = Path(os.environ.get("TRACEFIX_BUNDLE_DIR", "tracefix_bundle"))
APP_KIND = os.environ.get("TRACEFIX_APP_KIND", "monitor")
AGENT_ID = os.environ.get("TRACEFIX_AGENT_ID", "monitor")
AUTORUN = os.environ.get("TRACEFIX_AUTORUN", "0").lower() in {"1", "true", "yes", "on"}
TRACEFIX_MODEL = os.environ.get("TRACEFIX_MODEL", "").strip()
TRACEFIX_OPENCODE_BIN = os.environ.get("TRACEFIX_OPENCODE_BIN", "opencode").strip() or "opencode"
TRACEFIX_TIMEOUT = os.environ.get("TRACEFIX_TIMEOUT", "600").strip() or "600"


def _read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


class TraceFixMonitorApp(CityosServicer):
    def __init__(self):
        super().__init__()
        self.plan = _read_json(BUNDLE_DIR / "plan.json", {})
        self.agent = _read_json(BUNDLE_DIR / "agent.json", {})

    async def on_started(self) -> None:
        logging.info("TraceFix %s app started: %s", APP_KIND, AGENT_ID)
        logging.info("TraceFix bundle: %s", BUNDLE_DIR)
        await self._write_readiness()
        if AUTORUN:
            if APP_KIND == "monitor":
                asyncio.create_task(self._run_tracefix_workspace())
            else:
                logging.info(
                    "TRACEFIX_AUTORUN is enabled, but per-agent distributed execution "
                    "requires a shared TRACEFIX_COORD_URL. This package contains the "
                    "agent prompt and verified bundle; run the monitor app for full "
                    "single-container execution today."
                )

    async def _run_tracefix_workspace(self) -> None:
        workspace = BUNDLE_DIR / "workspace"
        command = [
            sys.executable,
            "-u",
            "-B",
            "-m",
            "tracefix.runtime.cli",
            "run",
            "--local-dev",
            "--workspace",
            str(workspace),
            "--harness",
            "opencode",
            "--verbose",
            "--opencode-bin",
            TRACEFIX_OPENCODE_BIN,
            "--timeout",
            TRACEFIX_TIMEOUT,
        ]
        if TRACEFIX_MODEL:
            command[command.index("--harness"):command.index("--harness")] = ["--model", TRACEFIX_MODEL]
        logging.info("Starting bundled TraceFix runtime: %s", " ".join(command))
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=str(BUNDLE_DIR),
        )
        assert process.stdout is not None
        async for raw in process.stdout:
            logging.info("[tracefix-runtime] %s", raw.decode("utf-8", "replace").rstrip())
        code = await process.wait()
        logging.info("Bundled TraceFix runtime exited with code %s", code)

    async def _write_readiness(self) -> None:
        ready_path = Path("/run/cityos") / f"tracefix_{APP_KIND}_{AGENT_ID}_ready.json"
        ready_path.parent.mkdir(parents=True, exist_ok=True)
        ready_path.write_text(json.dumps({
            "kind": APP_KIND,
            "agent": AGENT_ID,
            "bundle": str(BUNDLE_DIR),
            "plan_version": self.plan.get("version"),
            "started_at": datetime.utcnow().isoformat() + "Z",
        }, indent=2))

    async def receive_frame(self, stream_name: str, input_path: Path, timestamp: datetime) -> None:
        logging.info(
            "TraceFix %s app %s received CityOS frame stream=%s path=%s timestamp=%s",
            APP_KIND,
            AGENT_ID,
            stream_name,
            input_path,
            timestamp,
        )


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    asyncio.run(TraceFixMonitorApp().start())
