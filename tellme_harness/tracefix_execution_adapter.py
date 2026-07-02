"""Run TraceFix's real formal-verification pipeline on a built workspace.

Pipeline (TraceFix's own modules, in their intended order):

    spec/ir.json
      -> generate_pluscal_scaffold   (Protocol.tla)      [pure Python]
      -> generate_tlc_config         (Protocol.cfg)       [pure Python]
      -> translate_pluscal           (pcal.trans)         [Java + jar]
      -> run_tlc                     (TLC model check)    [Java + jar]

This adapter does NOT reimplement any of those stages — it imports and calls
TraceFix's functions. State extraction (``extract_states``) additionally needs
tree-sitter and is intentionally left to a separate, optionally-gated step.

IMPORTANT HONESTY NOTE: the deterministic IR scaffold carries only *structural*
invariants (TypeInvariant / ChannelsDrained). The coordination ordering logic
lives in PlusCal process bodies that the LLM/opencode design step fills, so a
``verified`` result here means "the structural scaffold model-checks", not "the
entry→impact→correlation→claim ordering is proven". This is recorded in
``verification_scope`` so the gate never overclaims.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Optional, Tuple

from .schemas import TraceFixVerificationResult
from .tracefix_toolchain import detect_tracefix_toolchain, _find_tracefix_root

_MAX_LOG_BYTES = 200_000


def _bounded(text: str) -> str:
    if text is None:
        return ""
    data = text.encode("utf-8", errors="ignore")
    if len(data) <= _MAX_LOG_BYTES:
        return text
    return data[:_MAX_LOG_BYTES].decode("utf-8", errors="ignore") + "\n...[truncated]\n"


def _import_pipeline():
    root = _find_tracefix_root()
    if root is None:
        return None
    root_str = str(root)
    if root_str not in sys.path:
        sys.path.insert(0, root_str)
    try:
        from tracefix.pipeline.pipeline.pluscal_generator import (
            generate_pluscal_scaffold,
            generate_tlc_config,
        )
        from tracefix.pipeline.pipeline.pluscal_compiler import translate_pluscal
        from tracefix.pipeline.pipeline.tlc_runner import run_tlc

        return generate_pluscal_scaffold, generate_tlc_config, translate_pluscal, run_tlc
    except Exception:
        return None


def _invariants_from_cfg(cfg: str) -> list[str]:
    out: list[str] = []
    for line in cfg.splitlines():
        line = line.strip()
        if line.startswith("INVARIANT "):
            out.append(line[len("INVARIANT "):].strip())
    return out


class TraceFixExecutionAdapter:
    def verify_workspace(self, *, workspace: Path) -> TraceFixVerificationResult:
        workspace = Path(workspace)
        started = time.monotonic()
        toolchain = detect_tracefix_toolchain()

        def _result(**kwargs) -> TraceFixVerificationResult:
            kwargs.setdefault("workspace_path", str(workspace))
            kwargs.setdefault("toolchain", toolchain)
            kwargs["duration_ms"] = int((time.monotonic() - started) * 1000)
            return TraceFixVerificationResult(**kwargs)

        if not toolchain.verification_available:
            return _result(
                status="toolchain_unavailable",
                verified=False,
                executable=False,
                failure_stage="toolchain",
                sanitized_error="; ".join(toolchain.blockers) or "verification toolchain unavailable",
            )

        ir_path = workspace / "spec" / "ir.json"
        if not ir_path.is_file():
            return _result(status="artifact_missing", failure_stage="ir", sanitized_error="spec/ir.json not found")

        pipeline = _import_pipeline()
        if pipeline is None:
            return _result(
                status="toolchain_unavailable",
                failure_stage="import",
                sanitized_error="TraceFix pipeline modules could not be imported",
            )
        generate_pluscal_scaffold, generate_tlc_config, translate_pluscal, run_tlc = pipeline

        output_dir = workspace / "output"
        output_dir.mkdir(parents=True, exist_ok=True)

        try:
            ir = json.loads(ir_path.read_text(encoding="utf-8"))
            tla = generate_pluscal_scaffold(ir)
            cfg = generate_tlc_config(ir)
        except Exception as exc:
            return _result(status="generation_failed", failure_stage="generation", sanitized_error=_sanitize(exc))

        tla_path = output_dir / "Protocol.tla"
        cfg_path = output_dir / "Protocol.cfg"
        tla_path.write_text(tla, encoding="utf-8")
        cfg_path.write_text(cfg, encoding="utf-8")
        invariants = _invariants_from_cfg(cfg)

        java_path = toolchain.java_path
        jar_path = toolchain.tla2tools_path

        try:
            translation = translate_pluscal(tla, cfg, java_path=java_path, tla2tools_jar=jar_path)
        except Exception as exc:
            return _result(
                status="translation_failed",
                failure_stage="translation",
                protocol_tla_path=str(tla_path),
                protocol_cfg_path=str(cfg_path),
                invariants_checked=invariants,
                sanitized_error=_sanitize(exc),
            )
        if not getattr(translation, "success", False):
            self._write_log(output_dir / "tlc_stderr.log", getattr(translation, "error_message", "") or "")
            return _result(
                status="translation_failed",
                failure_stage="translation",
                protocol_tla_path=str(tla_path),
                protocol_cfg_path=str(cfg_path),
                invariants_checked=invariants,
                sanitized_error=_bounded(getattr(translation, "error_message", "") or "pcal.trans failed"),
            )

        translated_path = output_dir / "Protocol_translated.tla"
        translated_path.write_text(translation.translated_tla, encoding="utf-8")

        try:
            tlc = run_tlc(translation.translated_tla, cfg, java_path=java_path, tla2tools_jar=jar_path)
        except Exception as exc:
            return _result(
                status="tlc_failed",
                failure_stage="tlc",
                protocol_tla_path=str(tla_path),
                protocol_cfg_path=str(cfg_path),
                translated_tla_path=str(translated_path),
                invariants_checked=invariants,
                sanitized_error=_sanitize(exc),
            )

        raw_output = getattr(tlc, "raw_output", "") or ""
        self._write_log(output_dir / "tlc_stdout.log", raw_output)
        stats = getattr(tlc, "stats", {}) or {}
        state_count = _stat_int(stats, ("states", "generated", "states_generated"))
        distinct = _stat_int(stats, ("distinct", "distinct_states"))

        common = dict(
            protocol_tla_path=str(tla_path),
            protocol_cfg_path=str(cfg_path),
            translated_tla_path=str(translated_path),
            invariants_checked=invariants,
            state_count=state_count,
            distinct_state_count=distinct,
        )

        if getattr(tlc, "success", False):
            return _result(status="verified", verified=True, executable=True, **common)

        violation = getattr(tlc, "violation_type", None)
        if violation in {"safety", "liveness", "deadlock"}:
            return _result(
                status="counterexample",
                verified=False,
                executable=False,
                counterexample_summary=_bounded(getattr(tlc, "error_trace", "") or violation),
                failure_stage="tlc",
                **common,
            )
        return _result(
            status="tlc_failed",
            verified=False,
            executable=False,
            failure_stage="tlc",
            sanitized_error=_bounded(getattr(tlc, "error_trace", "") or "TLC reported failure"),
            **common,
        )

    def _write_log(self, path: Path, content: str) -> None:
        try:
            path.write_text(_bounded(content), encoding="utf-8")
        except OSError:
            pass


def _stat_int(stats: dict, keys: Tuple[str, ...]) -> Optional[int]:
    for key in keys:
        value = stats.get(key)
        if isinstance(value, int):
            return value
        if isinstance(value, str) and value.isdigit():
            return int(value)
    return None


def _sanitize(exc: Exception) -> str:
    """Coarse, secret-free error category (never includes paths/keys/env)."""
    return f"{type(exc).__name__}"
