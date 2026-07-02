"""Persistent progress detection for model-driven PlusCal/TLC repair loops."""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tracefix.textio import safe_read_json


STATE_FILE = "repair_progress.json"


@dataclass(frozen=True)
class RepairConfig:
    max_attempts: int = 10
    repeated_error_limit: int = 2
    no_change_limit: int = 2
    time_budget_seconds: float = 600.0

    @classmethod
    def from_environment(cls) -> "RepairConfig":
        return cls(
            max_attempts=_env_int("TRACEFIX_MAX_REPAIR_ATTEMPTS", 10),
            repeated_error_limit=_env_int("TRACEFIX_REPEATED_ERROR_LIMIT", 2),
            no_change_limit=_env_int("TRACEFIX_NO_CHANGE_LIMIT", 2),
            time_budget_seconds=_env_float(
                "TRACEFIX_REPAIR_TIME_BUDGET_SECONDS",
                600.0,
            ),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "max_attempts": self.max_attempts,
            "repeated_error_limit": self.repeated_error_limit,
            "no_change_limit": self.no_change_limit,
            "time_budget_seconds": self.time_budget_seconds,
        }


@dataclass
class RepairContext:
    attempt: int
    is_repair: bool
    protocol_hash_before: str
    protocol_hash_after: str
    meaningful_hash_before: str
    meaningful_hash_after: str
    protocol_changed: bool
    meaningful_changed: bool
    repair_duration_ms: float
    blocked: bool = False
    stop_reason: str | None = None


@dataclass
class RepairDecision:
    stop: bool
    stop_reason: str | None
    recommendation: str
    attempt: dict[str, Any] | None = None


class RepairProgressTracker:
    """Track repair progress across separate ``verify`` CLI processes."""

    def __init__(
        self,
        spec_dir: Path,
        *,
        config: RepairConfig | None = None,
    ) -> None:
        self.spec_dir = Path(spec_dir).resolve()
        self.state_path = self.spec_dir / STATE_FILE
        self.config = config or RepairConfig.from_environment()
        self.state = self._load()

    def begin(
        self,
        protocol: str,
        *,
        now: datetime | None = None,
    ) -> RepairContext:
        now = now or datetime.now(timezone.utc)
        raw_hash = protocol_hash(protocol)
        semantic_hash = meaningful_protocol_hash(protocol)
        previous = self.state.get("last_verification") or {}
        attempts = self.state.setdefault("attempts", [])

        if previous.get("success"):
            self.state = self._new_state()
            previous = {}
            attempts = self.state["attempts"]

        previous_raw = str(previous.get("protocol_hash") or "")
        previous_semantic = str(previous.get("meaningful_protocol_hash") or "")
        is_repair = bool(previous and not previous.get("success"))
        repair_duration_ms = _elapsed_ms(previous.get("finished_at"), now) if is_repair else 0.0
        context = RepairContext(
            attempt=len(attempts) + 1 if is_repair else 0,
            is_repair=is_repair,
            protocol_hash_before=previous_raw,
            protocol_hash_after=raw_hash,
            meaningful_hash_before=previous_semantic,
            meaningful_hash_after=semantic_hash,
            protocol_changed=not is_repair or raw_hash != previous_raw,
            meaningful_changed=not is_repair or semantic_hash != previous_semantic,
            repair_duration_ms=repair_duration_ms,
        )

        if not is_repair:
            return context

        # A substantive manual redesign reopens a previously stopped workspace.
        if self.state.get("stop_reason") and context.meaningful_changed:
            self.state["stop_reason"] = None
            self.state["recommendation"] = None

        reason = self._preverification_stop(context)
        if reason:
            context.blocked = True
            context.stop_reason = reason
            attempt = self._attempt_record(
                context,
                error_category=str(previous.get("error_category") or ""),
                error_signature=str(previous.get("error_signature") or ""),
                normalized_error=str(previous.get("normalized_error") or ""),
                error_count=int(previous.get("error_count") or 1),
                progress_level=int(previous.get("progress_level") or 0),
                verification_duration_ms=0.0,
                verification_progressed=False,
                error_changed=False,
                progress_detected=False,
                stop_reason=reason,
            )
            attempts.append(attempt)
            self._stop(reason)
            self._save()
        return context

    def finish(
        self,
        context: RepairContext,
        *,
        success: bool,
        error_category: str = "",
        error_text: str = "",
        progress_level: int,
        verification_duration_ms: float,
        now: datetime | None = None,
    ) -> RepairDecision:
        now = now or datetime.now(timezone.utc)
        previous = self.state.get("last_verification") or {}
        normalized = normalize_error(error_text) if not success else ""
        signature = error_signature(error_text) if not success else ""
        count = error_count(error_text) if not success else 0
        previous_count = int(previous.get("error_count") or 0)
        previous_level = int(previous.get("progress_level") or 0)
        error_changed = bool(
            context.is_repair
            and signature
            and signature != str(previous.get("error_signature") or "")
        )
        category_changed = bool(
            context.is_repair
            and error_category
            and error_category != str(previous.get("error_category") or "")
        )
        verification_progressed = bool(
            success
            or progress_level > previous_level
            or (count > 0 and previous_count > 0 and count < previous_count)
        )
        progress_detected = bool(
            success
            or category_changed
            or error_changed
            or context.meaningful_changed
            or verification_progressed
        )

        attempt: dict[str, Any] | None = None
        if context.is_repair:
            attempt = self._attempt_record(
                context,
                error_category=error_category,
                error_signature=signature,
                normalized_error=normalized,
                error_count=count,
                progress_level=progress_level,
                verification_duration_ms=verification_duration_ms,
                verification_progressed=verification_progressed,
                error_changed=error_changed,
                progress_detected=progress_detected,
                stop_reason=None,
            )
            self.state.setdefault("attempts", []).append(attempt)

        self.state["last_verification"] = {
            "finished_at": now.isoformat(),
            "success": success,
            "error_category": error_category,
            "error_signature": signature,
            "normalized_error": normalized,
            "error_count": count,
            "progress_level": progress_level,
            "protocol_hash": context.protocol_hash_after,
            "meaningful_protocol_hash": context.meaningful_hash_after,
        }
        if context.is_repair:
            self.state["repair_time_seconds"] = round(
                float(self.state.get("repair_time_seconds") or 0.0)
                + (context.repair_duration_ms + verification_duration_ms) / 1000.0,
                3,
            )

        reason = "tlc_passed" if success else self._postverification_stop()
        if reason:
            self._stop(reason)
            if attempt is not None:
                attempt["stop_reason"] = reason
        else:
            self.state["stop_reason"] = None
            self.state["recommendation"] = None
        self._save()
        return RepairDecision(
            stop=bool(reason),
            stop_reason=reason,
            recommendation=str(self.state.get("recommendation") or ""),
            attempt=attempt,
        )

    def stop_message(self, reason: str | None = None) -> str:
        reason = reason or str(self.state.get("stop_reason") or "repair_stalled")
        recommendation = str(
            self.state.get("recommendation")
            or "Review Protocol.tla and the latest tlc_error.md manually."
        )
        return (
            f"REPAIR_STOPPED: {reason}. "
            f"TraceFix did not mark the protocol verified. {recommendation}"
        )

    def _preverification_stop(self, context: RepairContext) -> str | None:
        attempts = self.state.get("attempts") or []
        if len(attempts) >= self.config.max_attempts:
            return "max_repair_attempts_reached"
        projected_seconds = (
            float(self.state.get("repair_time_seconds") or 0.0)
            + context.repair_duration_ms / 1000.0
        )
        if projected_seconds > self.config.time_budget_seconds:
            return "repair_time_budget_exceeded"
        if not context.meaningful_changed:
            previous_no_change = _trailing_count(
                attempts,
                lambda item: not item.get("meaningful_protocol_changed", False),
            )
            if previous_no_change + 1 >= self.config.no_change_limit:
                return "no_meaningful_protocol_change"
        if not context.protocol_changed:
            previous_same_hash = _trailing_count(
                attempts,
                lambda item: not item.get("protocol_changed", False),
            )
            if previous_same_hash + 1 >= self.config.no_change_limit:
                return "protocol_hash_unchanged"
        return None

    def _postverification_stop(self) -> str | None:
        attempts = self.state.get("attempts") or []
        if len(attempts) >= self.config.max_attempts:
            return "max_repair_attempts_reached"
        if float(self.state.get("repair_time_seconds") or 0.0) > self.config.time_budget_seconds:
            return "repair_time_budget_exceeded"
        recent = attempts[-self.config.repeated_error_limit :]
        if len(recent) == self.config.repeated_error_limit:
            signatures = {item.get("error_signature") for item in recent}
            if (
                len(signatures) == 1
                and None not in signatures
                and all(not item.get("meaningful_protocol_changed", False) for item in recent)
            ):
                return "repeated_error_without_meaningful_change"
            if all(not item.get("verification_progressed", False) for item in recent):
                if all(not item.get("progress_detected", False) for item in recent):
                    return "verification_not_progressing"
        return None

    def _attempt_record(
        self,
        context: RepairContext,
        *,
        error_category: str,
        error_signature: str,
        normalized_error: str,
        error_count: int,
        progress_level: int,
        verification_duration_ms: float,
        verification_progressed: bool,
        error_changed: bool,
        progress_detected: bool,
        stop_reason: str | None,
    ) -> dict[str, Any]:
        return {
            "attempt": context.attempt,
            "duration_ms": round(
                context.repair_duration_ms + verification_duration_ms,
                2,
            ),
            "repair_duration_ms": round(context.repair_duration_ms, 2),
            "verification_duration_ms": round(verification_duration_ms, 2),
            "error_category": error_category,
            "error_signature": error_signature,
            "normalized_error": normalized_error[:500],
            "error_count": error_count,
            "protocol_hash_before": context.protocol_hash_before,
            "protocol_hash_after": context.protocol_hash_after,
            "meaningful_protocol_hash_before": context.meaningful_hash_before,
            "meaningful_protocol_hash_after": context.meaningful_hash_after,
            "protocol_changed": context.protocol_changed,
            "meaningful_protocol_changed": context.meaningful_changed,
            "error_changed": error_changed,
            "verification_progressed": verification_progressed,
            "progress_level": progress_level,
            "progress_detected": progress_detected,
            "stop_reason": stop_reason,
        }

    def _stop(self, reason: str) -> None:
        recommendations = {
            "tlc_passed": "No further repair is required.",
            "max_repair_attempts_reached": "Review the IR/protocol design before allowing more attempts.",
            "repair_time_budget_exceeded": "Inspect provider latency and the latest compiler error before retrying.",
            "no_meaningful_protocol_change": "Make a substantive protocol edit or redesign the affected process.",
            "protocol_hash_unchanged": "The repair produced no file change; inspect the model/tool edit result.",
            "repeated_error_without_meaningful_change": "Use the latest compiler line/context to redesign the smallest failing region.",
            "verification_not_progressing": "Review the protocol manually or revise the structured IR semantics.",
        }
        self.state["stop_reason"] = reason
        self.state["recommendation"] = recommendations.get(
            reason,
            "Review Protocol.tla and the latest tlc_error.md manually.",
        )

    def _new_state(self) -> dict[str, Any]:
        return {
            "version": "0.1",
            "config": self.config.to_dict(),
            "attempts": [],
            "repair_time_seconds": 0.0,
            "last_verification": {},
            "stop_reason": None,
            "recommendation": None,
        }

    def _load(self) -> dict[str, Any]:
        payload = safe_read_json(self.state_path, {})
        if not isinstance(payload, dict):
            payload = {}
        state = self._new_state()
        state.update(payload)
        state["config"] = self.config.to_dict()
        if not isinstance(state.get("attempts"), list):
            state["attempts"] = []
        return state

    def _save(self) -> None:
        self.spec_dir.mkdir(parents=True, exist_ok=True)
        temporary = self.state_path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(self.state, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        temporary.replace(self.state_path)


def protocol_hash(protocol: str) -> str:
    return hashlib.sha256(protocol.encode("utf-8")).hexdigest()


def meaningful_protocol_hash(protocol: str) -> str:
    without_blocks = re.sub(r"\(\*.*?\*\)", "", protocol, flags=re.DOTALL)
    without_lines = re.sub(r"(?m)\\\*.*$", "", without_blocks)
    normalized = re.sub(r"\s+", "", without_lines)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def normalize_error(error: str) -> str:
    value = error.lower().replace("\\", "/")
    value = re.sub(r"[a-z]:/[^\s:]+", "<path>", value)
    value = re.sub(r"\b(line|column|position|state)\s*[:#]?\s*\d+\b", r"\1 <n>", value)
    value = re.sub(r"\b\d+\b", "<n>", value)
    value = re.sub(r"\s+", " ", value).strip()
    return value


def error_signature(error: str) -> str:
    normalized = normalize_error(error)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:16] if normalized else ""


def error_count(error: str) -> int:
    lines = {
        line.strip().lower()
        for line in error.splitlines()
        if re.search(r"\b(error|violation|expected|deadlock|failed)\b", line, re.IGNORECASE)
    }
    return max(1, len(lines)) if error.strip() else 0


def _elapsed_ms(value: Any, now: datetime) -> float:
    if not value:
        return 0.0
    try:
        previous = datetime.fromisoformat(str(value))
        if previous.tzinfo is None:
            previous = previous.replace(tzinfo=timezone.utc)
        return max(0.0, (now - previous).total_seconds() * 1000.0)
    except ValueError:
        return 0.0


def _trailing_count(items: list[dict[str, Any]], predicate) -> int:
    count = 0
    for item in reversed(items):
        if not predicate(item):
            break
        count += 1
    return count


def _env_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, str(default))))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return max(0.001, float(os.getenv(name, str(default))))
    except ValueError:
        return default
