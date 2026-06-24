"""``tracefix design`` — drive an UNMODIFIED headless opencode through the
/tla-verify-pluscal skill to turn a natural-language requirement into a
verified workspace (spec/ + prompts/runtime_b/ + spec/cityos_module_plan.json).

Architecture: the design *knowledge* lives in the skill (SKILL.md + references)
and the *checks* live in the ``tla-verify-pluscal`` CLI — both harness-portable.
opencode is just the engine: this orchestrator injects the skill as the agent's
system prompt (plus a headless preamble), points ``--dir`` at the repo root (so
``.claude/skills/`` references and ``workspace/`` resolve), and judges the
outcome from the ARTIFACTS the run leaves behind, never from the transcript.

No opencode source modification — same principle as the runtime adapter.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from tracefix.runtime.opencode_adapter.config_gen import build_design_config
from tracefix.runtime.opencode_adapter.driver import run_opencode_agent

#: Where the design-workflow skills live, relative to the repo root.
_SKILL = ".claude/skills/tla-verify-pluscal/SKILL.md"
_PROMPT_GEN_SKILL = ".claude/skills/tla-prompt-gen/SKILL.md"

#: A full design+verify+prompts run includes several TLC invocations and up to
#: 5 repair attempts — give it real time by default.
DEFAULT_TIMEOUT = 1800.0


def _subprocess_python() -> str:
    return os.environ.get("TRACEFIX_PYTHON_EXE", "").strip() or sys.executable


def repo_root() -> Path:
    return next(p for p in Path(__file__).resolve().parents
                if (p / "pyproject.toml").exists())


def slugify(task: str) -> str:
    """A short filesystem-safe workspace name derived from the task text."""
    words = re.findall(r"[a-z0-9]+", task.lower())[:5]
    return "_".join(words) or "design"


_HEADLESS_PREAMBLE = """\
# Headless mode (no interactive user)

You are running non-interactively inside `tracefix design`. Follow the workflow
below with these adjustments:
- NEVER pause to ask the user anything. At the Phase 1.5 review gate, write
  `plan.md` into the workspace and proceed immediately (it is kept for
  after-the-fact review).
- The requirement may be plain prose with no explicit agent/resource lists.
  Derive the coordination structure yourself, and record EVERY structural
  choice the prose does not state outright — how many agents and why, what
  each shared resource is and why it is exclusive (Lock) vs a capacity pool
  (Counter), and the channel topology — in `plan.md` under an
  `## Assumptions` heading. Unrecorded assumptions count as silent guessing.
- The IR schema allows ONLY these topology fields: `agents`, `resources`, and
  `channels` (plus documented planner metadata such as `state_tasks`,
  `agent_resources`, and `tool_resource_map`). Do NOT emit top-level or nested
  `locks`, `counters`, `permissions`, `edges`, `messages`, or other ad hoc
  schema fields. Lock-like behavior is encoded as
  `{"id": "RESOURCE_ID", "type": "Lock"}` inside `resources`; counter-like
  behavior is encoded as `{"id": "POOL_ID", "type": "Counter", "config":
  {"initial": N}}` inside `resources`.
- Typed tools: most domain work runs on builtins (read/write/edit/bash). If a
  step needs a structured typed tool (a real external API, or custom typed
  logic), tag it in PlusCal `[tool: ...; impl: external|local]` and list it
  (name, owning agent, impl kind) under `## Assumptions` — extract-states turns
  the tags into a generated `tools.json` + impl stub the user binds later.
- "Never pause" means do NOT ask the user — it does NOT mean skip the checks.
  Still do the SKILL.md Phase 1.5 self-critique (verify every hazard, channel,
  ordering constraint, and failure path is represented against the requirement)
  and the MANDATORY Phase 2.5 fidelity check before verifying.
- Where the workflow says to invoke the `/tla-prompt-gen` skill (Phase 5), read
  `{prompt_gen_skill}` and follow it directly on this same workspace instead.
- The skill's reference files live under `.claude/skills/tla-verify-pluscal/references/`
  — read them when the workflow points to them.
- Work ONLY inside the workspace directory given in the task message; run the
  `tla-verify-pluscal` CLI via bash (it is on PATH).
- If verification still fails after 5 attempts, record `"tlc_passed": false` in
  `summary.json`, write an honest failure report, and stop — never fake a pass.

---

"""


def build_designer_prompt(root: Path) -> str:
    """The designer agent's system prompt: headless preamble + the skill itself."""
    skill_text = (root / _SKILL).read_text(encoding="utf-8")
    # Strip the YAML frontmatter (invocation metadata, meaningless to opencode).
    if skill_text.startswith("---"):
        end = skill_text.find("\n---", 3)
        if end != -1:
            skill_text = skill_text[end + 4:]
    preamble = _HEADLESS_PREAMBLE.replace("{prompt_gen_skill}", _PROMPT_GEN_SKILL)
    return preamble + skill_text.lstrip("\n")


def design_kickoff(workspace_rel: str) -> str:
    return (
        f"Design, verify, and generate prompts for the coordination protocol of the "
        f"task described in `{workspace_rel}/description.md`. The workspace is "
        f"`{workspace_rel}/` (already initialized: spec/ir.json is a stub to replace). "
        "Before scaffolding PlusCal/TLC, replace the stub with a complete IR. "
        "The IR must contain only schema-allowed fields. Do not write `locks` "
        "or `counters`; encode them as `resources` entries with type `Lock` "
        "or `Counter`. "
        "If the IR keeps two or more agents, `channels` must be non-empty: add "
        "the minimal directed FIFO channels required by task handoffs, shared "
        "resource coordination, review/approval flow, data dependencies, or "
        "failure/retry paths. Each channel must include id, from, to, and a "
        "non-empty labels list, and endpoints must match agent ids. Do not add "
        "arbitrary complete-graph channels. If the task is truly independent "
        "and needs no communication, collapse it to a single-agent design or "
        "document why in plan.md before continuing. "
        f"Follow your instructions end to end — finish only when "
        f"`{workspace_rel}/prompts/runtime_b/` holds one prompt per agent."
    )


def ir_repair_kickoff(workspace_rel: str, errors: list[str]) -> str:
    error_text = "\n".join(f"- {error}" for error in errors) or "- unknown IR validation failure"
    return (
        f"Repair ONLY `{workspace_rel}/spec/ir.json`.\n\n"
        f"Current IR validation errors:\n{error_text}\n\n"
        "Rules:\n"
        "- Do not remove agents.\n"
        "- Do not remove resources.\n"
        "- The IR must contain only schema-allowed fields. Do not add `locks`, "
        "`counters`, `permissions`, `edges`, `messages`, or other ad hoc fields.\n"
        "- Lock-like behavior belongs in `resources` as {\"id\": \"...\", \"type\": \"Lock\"}; "
        "counter-like behavior belongs in `resources` as type `Counter` with config.initial.\n"
        "- Normalize agents to objects like {\"id\": \"DEVELOPER_A\"} if needed.\n"
        "- If two or more agents remain, channels must be non-empty.\n"
        "- Infer the minimal directed FIFO channels from task handoffs, shared-resource "
        "coordination, review/approval flow, data dependencies, or failure/retry paths.\n"
        "- Do not add arbitrary complete-graph channels just to satisfy validation.\n"
        "- If agents are truly independent, collapse to a single-agent IR and explain why.\n"
        "- Each channel must include id, from, to, and labels.\n"
        "- labels must be a non-empty list of task-relevant message labels.\n"
        "- Every channel endpoint must reference an existing agent id.\n"
        "- Preserve JSON validity.\n"
        "- The IR must be scaffoldable by tla-verify-pluscal.\n"
        "- Write spec/ir_repair_notes.md listing each channel added and why.\n"
        "- Do not edit Protocol.tla, Protocol.cfg, states.json, prompts, or runtime files.\n\n"
        "After writing spec/ir.json, call validate_ir(). Stop after the IR validates."
    )


def pluscal_completion_kickoff(workspace_rel: str) -> str:
    return (
        f"Continue the TraceFix design workflow for `{workspace_rel}/`.\n\n"
        "TraceFix has already validated `spec/ir.json` and deterministically "
        "generated `spec/Protocol.tla` plus `spec/Protocol.cfg` from that IR. "
        "Do not redesign the IR unless verify reports a concrete IR problem.\n\n"
        "Required next steps:\n"
        "- Read `spec/Protocol.tla` and the PlusCal rules before editing.\n"
        "- Replace the scaffolded process-body placeholders with faithful "
        "PlusCal coordination logic for every agent.\n"
        "- Run `tla-verify-pluscal verify` on the spec directory.\n"
        "- If TLC fails, repair `Protocol.tla` and verify again, up to the "
        "workflow's repair limit.\n"
        "- After TLC passes, extract states and follow the prompt-generation "
        "phase for this same workspace.\n"
        "- Finish only when `prompts/runtime_b/` contains one prompt per agent."
    )


@dataclass
class DesignResult:
    """Artifact-judged outcome of one design run."""
    success: bool
    workspace: str
    status: str                       # ready | ir_incomplete | pluscal_error | tlc_error | timeout | error
    tlc_passed: bool | None = None
    agents: list[str] = field(default_factory=list)
    prompts: list[str] = field(default_factory=list)
    repairs: int | None = None
    duration: float = 0.0
    events: int = 0
    stderr_tail: list[str] = field(default_factory=list)
    ir_errors: list[str] = field(default_factory=list)
    diagnostics: list[str] = field(default_factory=list)


def inspect_workspace(ws: Path) -> dict:
    """Judge a design run purely from its artifacts (transcript-independent)."""
    spec = ws / "spec" if (ws / "spec").is_dir() else ws
    out: dict = {"ir": None, "states": (spec / "states.json").exists(),
                 "tlc_passed": None, "repairs": None, "prompts": [],
                 "protocol": (spec / "Protocol.tla").exists(),
                 "tlc_error": (spec / "tlc_error.md").exists()}
    ir_path = spec / "ir.json"
    if ir_path.exists():
        try:
            out["ir"] = json.loads(ir_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    summary = spec / "summary.json"
    if summary.exists():
        try:
            s = json.loads(summary.read_text(encoding="utf-8"))
            out["tlc_passed"] = s.get("tlc_passed")
            out["repairs"] = s.get("total_repairs")
        except (json.JSONDecodeError, OSError):
            pass
    pdir = ws / "prompts" / "runtime_b"
    if pdir.is_dir():
        out["prompts"] = sorted(p.stem for p in pdir.glob("*.md"))
    return out


def _agent_ids(ir: dict | None) -> list[str]:
    if not isinstance(ir, dict):
        return []
    try:
        from tracefix.pipeline.pipeline.validator import normalize_ir

        ir = normalize_ir(ir)
    except Exception:
        pass
    agents = ir.get("agents", []) if isinstance(ir, dict) else []
    return [
        agent["id"]
        for agent in agents
        if isinstance(agent, dict) and isinstance(agent.get("id"), str)
    ]


def _spec_dir(ws: Path) -> Path:
    spec = ws / "spec"
    return spec if spec.is_dir() else ws


def _load_ir(ws: Path) -> dict | None:
    ir_path = _spec_dir(ws) / "ir.json"
    try:
        return json.loads(ir_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _channel_key(channel: dict) -> str:
    return str(channel.get("id") or f"{channel.get('from')}->{channel.get('to')}")


def _channel_diagnostics(before: dict | None, after: dict | None) -> list[str]:
    if not isinstance(after, dict):
        return ["IR repair diagnostics: spec/ir.json is unreadable after repair"]
    before_channels = before.get("channels", []) if isinstance(before, dict) else []
    after_channels = after.get("channels", [])
    if not isinstance(before_channels, list):
        before_channels = []
    if not isinstance(after_channels, list):
        return ["IR repair diagnostics: channels is not a list after repair"]

    before_keys = {
        _channel_key(channel)
        for channel in before_channels
        if isinstance(channel, dict)
    }
    added = [
        channel
        for channel in after_channels
        if isinstance(channel, dict) and _channel_key(channel) not in before_keys
    ]
    if not added:
        return ["IR repair diagnostics: no channels were added"]

    diagnostics = []
    for channel in added:
        labels = channel.get("labels", [])
        if isinstance(labels, list):
            label_text = ", ".join(str(label) for label in labels)
        else:
            label_text = str(labels)
        diagnostics.append(
            "IR repair added channel "
            f"{_channel_key(channel)}: {channel.get('from')} -> {channel.get('to')} "
            f"labels=[{label_text}]"
        )
    return diagnostics


def validate_design_ir(ws: Path) -> tuple[bool, list[str], list[str]]:
    """Normalize and validate spec/ir.json for design postflight."""
    from tracefix.pipeline.pipeline.validator import (
        normalize_ir_with_diagnostics,
        validate_ir,
    )

    spec = _spec_dir(ws)
    ir_path = spec / "ir.json"
    diagnostics = ["IR validation started"]
    if not ir_path.exists():
        return False, ["missing spec/ir.json"], diagnostics

    try:
        original = json.loads(ir_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return False, [f"invalid spec/ir.json: {exc}"], diagnostics

    normalized, normalize_diagnostics = normalize_ir_with_diagnostics(original)
    if normalized != original:
        ir_path.write_text(json.dumps(normalized, indent=2) + "\n", encoding="utf-8")
        diagnostics.append("IR normalized: agent/resource schemas canonicalized")
        diagnostics.extend(normalize_diagnostics)

    result = validate_ir(normalized)
    if result.valid:
        diagnostics.append("IR validation passed")
        return True, [], diagnostics

    diagnostics.append("IR validation failed")
    return False, list(result.errors), diagnostics


def _scaffold_valid_ir(ws: Path) -> list[str]:
    """Generate Protocol.tla/Protocol.cfg after postflight proves IR is valid."""
    from tracefix.pipeline.pipeline.pluscal_generator import (
        generate_pluscal_scaffold,
        generate_tlc_config,
    )
    from tracefix.pipeline.pipeline.validator import normalize_ir, validate_ir

    spec = _spec_dir(ws)
    ir_path = spec / "ir.json"
    ir_data = normalize_ir(json.loads(ir_path.read_text(encoding="utf-8")))
    result = validate_ir(ir_data)
    if not result.valid:
        errors = "; ".join(result.errors)
        raise ValueError(f"cannot scaffold invalid IR: {errors}")

    (spec / "ir.json").write_text(json.dumps(ir_data, indent=2) + "\n", encoding="utf-8")
    (spec / "Protocol.tla").write_text(generate_pluscal_scaffold(ir_data), encoding="utf-8")
    (spec / "Protocol.cfg").write_text(generate_tlc_config(ir_data), encoding="utf-8")
    return [
        "Valid IR had no Protocol.tla; deterministic scaffold fallback ran",
        "Scaffold fallback wrote Protocol.tla and Protocol.cfg",
    ]


def classify_design_artifacts(ws: Path, *, timed_out: bool = False) -> tuple[str, list[str], list[str]]:
    """Classify the design stage from artifacts without trusting transcript text."""
    if timed_out:
        return "timeout", [], ["Design timed out"]

    valid_ir, ir_errors, diagnostics = validate_design_ir(ws)
    spec = _spec_dir(ws)
    protocol_path = spec / "Protocol.tla"
    states_path = spec / "states.json"
    summary_path = spec / "summary.json"
    tlc_error_path = spec / "tlc_error.md"

    if not valid_ir:
        diagnostics.append("PlusCal scaffold skipped because IR is incomplete")
        diagnostics.append("TLC did not run")
        return "ir_incomplete", ir_errors, diagnostics

    if not protocol_path.exists():
        diagnostics.append("Protocol.tla missing after valid IR")
        return (
            "ir_incomplete",
            ["Design stopped before PlusCal scaffolding. TLC did not run."],
            diagnostics,
        )

    diagnostics.append("PlusCal scaffold artifact present")
    if summary_path.exists():
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            summary = {}
        if summary.get("tlc_passed") is True and states_path.exists():
            diagnostics.append("TLC passed and states extracted")
            return "ready", [], diagnostics
        if summary.get("tlc_passed") is False:
            diagnostics.append("TLC ran and failed")
            return "tlc_error", [], diagnostics

    if tlc_error_path.exists():
        diagnostics.append("TLC error artifact present")
        return "tlc_error", [], diagnostics

    if not states_path.exists():
        diagnostics.append("Protocol.tla exists but states.json is missing")
        return (
            "pluscal_error",
            ["Protocol.tla exists, but TLC/states extraction did not complete."],
            diagnostics,
        )

    return "incomplete", [], diagnostics


def judge(ws: Path, *, timed_out: bool = False) -> DesignResult:
    info = inspect_workspace(ws)
    agents = _agent_ids(info["ir"])
    have_all_prompts = bool(agents) and set(agents) <= set(info["prompts"])
    ready = bool(info["tlc_passed"]) and info["states"] and have_all_prompts
    if ready:
        status = "ready"
    elif timed_out:
        status = "timeout"
    elif info["tlc_passed"] is False:
        status = "tlc_error"
    elif not info["protocol"]:
        status = "ir_incomplete"
    elif info["tlc_error"]:
        status = "tlc_error"
    else:
        status = "incomplete"
    return DesignResult(
        success=ready, workspace=str(ws), status=status,
        tlc_passed=info["tlc_passed"], agents=agents,
        prompts=info["prompts"], repairs=info["repairs"],
    )


def _init_workspace(name: str, task: str) -> Path:
    """Create a fresh workspace/<name>_<timestamp>/ via the CLI's init.

    resolve_init_dir does the timestamping (every design run gets a new
    directory, never iterating on an older same-named workspace); passing the
    resolved path to cmd_init keeps the two in agreement on where files land."""
    from tracefix.cli.cli import cmd_init, resolve_init_dir
    out = resolve_init_dir(name)
    cmd_init(argparse.Namespace(dir=str(out), task=task, agents=None, with_tools=False))
    return out.resolve()


class DesignWatcher:
    """Polls the workspace's ARTIFACTS and narrates design progress to the event bus.

    Observability only (the runtime three-plane rule applies here too): the watcher
    renders what the artifacts prove — ir.json written, TLC verdict, states.json,
    prompts appearing — and never influences the design run.
    """

    def __init__(self, ws: Path, bus, poll: float = 1.0):
        self.ws = ws
        self.bus = bus
        self.poll = poll
        self._ir_snapshot: str | None = None
        self._summary_snapshot: str | None = None
        self._prompts_seen: set[str] = set()
        self._states_done = False

    async def _emit(self, event: str, data: dict) -> None:
        await self.bus.emit(event, data)

    async def _tick(self) -> None:
        spec = self.ws / "spec" if (self.ws / "spec").is_dir() else self.ws

        ir_path = spec / "ir.json"
        if ir_path.exists():
            try:
                raw = ir_path.read_text(encoding="utf-8")
            except OSError:
                raw = None
            if raw and raw != self._ir_snapshot:
                try:
                    ir = json.loads(raw)
                except json.JSONDecodeError:
                    ir = None
                # the init stub has no resources/channels — only narrate a real IR
                if ir and ir.get("agents") and (ir.get("channels") or ir.get("resources")):
                    self._ir_snapshot = raw
                    await self._emit("design.phase",
                                     {"key": "ir", "state": "done", "label": "IR designed"})
                    await self._emit("design.ir", {"ir": ir})
                    await self._emit("design.phase",
                                     {"key": "pluscal", "state": "active",
                                      "label": "writing PlusCal bodies"})

        summary = spec / "summary.json"
        if summary.exists():
            try:
                raw = summary.read_text(encoding="utf-8")
            except OSError:
                raw = None
            if raw and raw != self._summary_snapshot:
                self._summary_snapshot = raw
                try:
                    s = json.loads(raw)
                except json.JSONDecodeError:
                    s = {}
                await self._emit("design.phase",
                                 {"key": "verify", "state": "active", "label": "TLC verify"})
                if "tlc_passed" in s and (s["tlc_passed"] or s.get("total_repairs")):
                    await self._emit("design.verdict",
                                     {"tlc_passed": s.get("tlc_passed"),
                                      "repairs": s.get("total_repairs")})

        if not self._states_done and (spec / "states.json").exists():
            self._states_done = True
            await self._emit("design.phase",
                             {"key": "states", "state": "done", "label": "states extracted"})

        pdir = self.ws / "prompts" / "runtime_b"
        if pdir.is_dir():
            total = len((json.loads(self._ir_snapshot) or {}).get("agents", [])) \
                if self._ir_snapshot else 0
            for p in sorted(pdir.glob("*.md")):
                if p.stem not in self._prompts_seen:
                    self._prompts_seen.add(p.stem)
                    await self._emit("design.prompt",
                                     {"agent": p.stem, "count": len(self._prompts_seen),
                                      "total": total or "?"})

    async def run(self) -> None:
        await self._emit("design.phase",
                         {"key": "toolchain", "state": "active", "label": "starting designer"})
        try:
            while True:
                await self._tick()
                await asyncio.sleep(self.poll)
        except asyncio.CancelledError:
            await self._tick()   # final sweep so late artifacts still render
            raise


async def run_design(
    task: str,
    *,
    name: str | None = None,
    model: str | None = None,
    opencode_cmd: list[str] | None = None,
    timeout: float = DEFAULT_TIMEOUT,
    on_event: Callable[[str, dict], None] | None = None,
    verbose: bool = False,
    live: bool = False,
    live_port: int = 8765,
    live_warmup: float = 3.0,
    live_hold: float = 0.0,
) -> DesignResult:
    """One full design run: requirement in, verified workspace out."""
    root = repo_root()
    name = name or slugify(task)
    ws = _init_workspace(name, task)
    ws_rel = str(ws.relative_to(root))

    prompt = build_designer_prompt(root)
    cfg = build_design_config(prompt, model=model)

    # Optional live view: same SSE stack as the runtime, design-phase page.
    bus = server = watcher_task = None
    if live:
        from tracefix.runtime.monitoring.event_bus import EventBus
        from tracefix.runtime.monitoring.live_server import start_live_server
        from tracefix.runtime.opencode_adapter.design_view import render_design_html

        bus = EventBus()
        server = await start_live_server(
            {}, bus, port=live_port,
            html=render_design_html(title=f"design: {name}", model=model or ""))
        url = f"http://127.0.0.1:{live_port}"
        print(f"[design] live view: {url}", file=sys.stderr)
        import webbrowser
        webbrowser.open(url)
        if live_warmup > 0:
            await asyncio.sleep(live_warmup)
        watcher_task = asyncio.create_task(DesignWatcher(ws, bus).run())

        user_on_event = on_event

        def _tap(agent_id: str, ev: dict) -> None:
            if user_on_event is not None:
                user_on_event(agent_id, ev)
            if ev.get("type") == "tool_use":
                part = ev.get("part") or {}
                asyncio.get_running_loop().create_task(bus.emit("design.tool", {
                    "tool": part.get("tool"),
                    "status": (part.get("state") or {}).get("status"),
                }))
        on_event = _tap

    # Same process hygiene as the runtime adapter: per-run XDG data/state (keep the
    # user's opencode state clean; cache stays shared for the warm ripgrep), and the
    # venv's bin on PATH so `tla-verify-pluscal` resolves inside opencode's bash.
    inst = ws / ".design"
    for sub in ("data", "state"):
        (inst / sub).mkdir(parents=True, exist_ok=True)
    venv_bin = str(Path(_subprocess_python()).resolve().parent)
    env = {
        "XDG_DATA_HOME": str(inst / "data"),
        "XDG_STATE_HOME": str(inst / "state"),
        "PATH": venv_bin + os.pathsep + os.environ.get("PATH", ""),
    }

    if verbose:
        print(f"[design] workspace={ws_rel} model={model or '(opencode default)'} "
              f"timeout={timeout:.0f}s", file=sys.stderr)

    start = time.time()
    disposition = await run_opencode_agent(
        "designer", cfg,
        opencode_cmd=opencode_cmd or ["opencode"],
        output_dir=root,                      # --dir = repo root: skills + workspace/ resolve
        kickoff=design_kickoff(ws_rel),
        timeout=timeout,
        on_event=on_event,
        env_overrides=env,
    )

    timed_out = disposition["status"] == "timeout"
    run_diagnostics = [
        f"Workspace initialized: {ws_rel}",
        "OpenCode design attempt finished",
    ]
    status, ir_errors, diagnostics = classify_design_artifacts(ws, timed_out=timed_out)
    diagnostics = [*run_diagnostics, *diagnostics]
    repair_disposition = None
    continuation_disposition = None
    if (
        status == "ir_incomplete"
        and not timed_out
        and any("before PlusCal scaffolding" in error for error in ir_errors)
    ):
        diagnostics.append("PlusCal scaffold fallback started")
        try:
            diagnostics.extend(_scaffold_valid_ir(ws))
        except Exception as exc:  # noqa: BLE001 - keep the design report actionable
            diagnostics.append(f"PlusCal scaffold fallback failed: {exc}")
        else:
            diagnostics.append("PlusCal continuation pass started")
            continuation_disposition = await run_opencode_agent(
                "designer",
                cfg,
                opencode_cmd=opencode_cmd or ["opencode"],
                output_dir=root,
                kickoff=pluscal_completion_kickoff(ws_rel),
                timeout=timeout,
                on_event=on_event,
                env_overrides=env,
            )
            diagnostics.append("PlusCal continuation pass finished")
            status, ir_errors, continuation_diagnostics = classify_design_artifacts(
                ws,
                timed_out=continuation_disposition["status"] == "timeout",
            )
            diagnostics.extend(continuation_diagnostics)

    if (
        status == "ir_incomplete"
        and not timed_out
        and any("no communication channels" in error for error in ir_errors)
    ):
        diagnostics.append("IR repair pass started")
        ir_before_repair = _load_ir(ws)
        repair_disposition = await run_opencode_agent(
            "designer_ir_repair",
            cfg,
            opencode_cmd=opencode_cmd or ["opencode"],
            output_dir=root,
            kickoff=ir_repair_kickoff(ws_rel, ir_errors),
            timeout=min(timeout, 600.0),
            on_event=on_event,
            env_overrides=env,
        )
        diagnostics.append("IR repair pass finished")
        diagnostics.extend(_channel_diagnostics(ir_before_repair, _load_ir(ws)))
        status, ir_errors, repair_diagnostics = classify_design_artifacts(
            ws,
            timed_out=repair_disposition["status"] == "timeout",
        )
        diagnostics.extend(repair_diagnostics)
        # If repair produced a valid IR but Protocol.tla still missing, scaffold now.
        if (
            status == "ir_incomplete"
            and repair_disposition["status"] != "timeout"
            and any("before PlusCal scaffolding" in error for error in ir_errors)
        ):
            diagnostics.append("IR valid after repair; starting PlusCal scaffold")
            try:
                diagnostics.extend(_scaffold_valid_ir(ws))
            except Exception as exc:  # noqa: BLE001
                diagnostics.append(f"PlusCal scaffold after repair failed: {exc}")
            else:
                diagnostics.append("PlusCal continuation pass started (post-repair)")
                post_repair_continuation = await run_opencode_agent(
                    "designer",
                    cfg,
                    opencode_cmd=opencode_cmd or ["opencode"],
                    output_dir=root,
                    kickoff=pluscal_completion_kickoff(ws_rel),
                    timeout=timeout,
                    on_event=on_event,
                    env_overrides=env,
                )
                diagnostics.append("PlusCal continuation pass finished (post-repair)")
                status, ir_errors, post_repair_diagnostics = classify_design_artifacts(
                    ws,
                    timed_out=post_repair_continuation["status"] == "timeout",
                )
                diagnostics.extend(post_repair_diagnostics)
                if repair_disposition is not None:
                    repair_disposition["events"] = (
                        repair_disposition.get("events", 0)
                        + post_repair_continuation.get("events", 0)
                    )

    result = judge(ws, timed_out=timed_out)
    if result.success:
        result.status = "ready"
    elif status != "ready":
        result.status = status
    result.ir_errors = ir_errors
    result.diagnostics = diagnostics
    result.duration = time.time() - start
    result.events = disposition.get("events", 0) + (
        repair_disposition.get("events", 0) if repair_disposition else 0
    ) + (
        continuation_disposition.get("events", 0) if continuation_disposition else 0
    )
    result.stderr_tail = [
        *disposition.get("stderr_tail", []),
        *(continuation_disposition.get("stderr_tail", []) if continuation_disposition else []),
        *(repair_disposition.get("stderr_tail", []) if repair_disposition else []),
    ]
    if result.status == "incomplete" and disposition.get("returncode") not in (0, None):
        result.status = "error"

    if result.success:
        from tracefix.runtime.cityos_plan import export_cityos_module_plan

        try:
            export_cityos_module_plan(ws)
        except Exception as e:  # noqa: BLE001 - design should report the export issue cleanly
            result.success = False
            result.status = "cityos_plan_failed"
            result.stderr_tail = [*result.stderr_tail, f"cityos plan export failed: {e}"]

    if live and bus is not None:
        if watcher_task is not None:
            watcher_task.cancel()
            try:
                await watcher_task
            except asyncio.CancelledError:
                pass
        await bus.emit("design.done", {"status": result.status, "workspace": ws_rel})
        if live_hold > 0:
            await asyncio.sleep(live_hold)
        else:
            await asyncio.sleep(1.0)  # let the browser drain the final events
        if server is not None:
            from tracefix.runtime.monitoring.live_server import stop_live_server
            await stop_live_server(server)

    return result
