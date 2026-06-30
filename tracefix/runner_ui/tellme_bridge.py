"""Repo-local bridge from TeLLMe planning artifacts to TraceFix design."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from tellme_harness import TellMeHarness
from tellme_harness.config import DEFAULT_OPENAI_MODEL, get_llm_config


class TellMeBridge:
    """Run TeLLMe and persist the current cross-stage handoff."""

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.runs_root = self.root / ".runs" / "tellme"
        self.current_path = self.runs_root / "current.json"

    def process_query(
        self,
        *,
        query: str,
        space_id: str | None = None,
        timestamp: str | None = None,
        backend_mode: str = "deterministic",
        llm_model: str = "",
        request_api_key: str | None = None,
    ) -> dict[str, Any]:
        query = query.strip()
        if not query:
            raise ValueError("A natural-language smart-room request is required.")
        if backend_mode not in {"deterministic", "fake_llm", "llm"}:
            raise ValueError("TeLLMe backend mode must be deterministic, fake_llm, or llm.")

        config = get_llm_config()
        effective_api_key = (request_api_key or "").strip() or config.api_key
        selected_model = llm_model.strip() or config.model or DEFAULT_OPENAI_MODEL
        if backend_mode == "llm" and not effective_api_key:
            raise ValueError(
                "LLM mode selected, but no API key was found. "
                "Set OPENAI_API_KEY or TELLME_API_KEY."
            )

        harness = TellMeHarness(
            runs_root=self.runs_root,
            agent_backend_mode=backend_mode,
            llm_api_key=effective_api_key if backend_mode == "llm" else None,
            llm_model=selected_model,
            execution_mode="planning_only",
        )
        answer = harness.handle_query(query, space_id=space_id, timestamp=timestamp)
        answer_payload = answer.model_dump()
        llm_metadata = ((answer_payload.get("raw_outputs") or {}).get("llm_operational") or {})
        decomposition = llm_metadata.get("decomposition") if isinstance(llm_metadata, dict) else {}
        if backend_mode == "llm" and isinstance(decomposition, dict) and decomposition.get("last_used") == "fallback":
            reason = decomposition.get("fallback_reason") or "unknown_llm_error"
            raise RuntimeError(
                f"TeLLMe LLM request failed ({reason}). "
                "Check the API key, model, and network connection, or use Deterministic mode."
            )
        run_dir = Path(str(answer.raw_outputs.get("run_dir") or "")).resolve()
        task_spec = answer_payload.get("tracefix_task_spec") or {}
        validation_policy = task_spec.get("validation_policy") if isinstance(task_spec, dict) else {}
        warnings = list(validation_policy.get("warnings") or []) if isinstance(validation_policy, dict) else []

        data = {
            "query_id": answer.query_id,
            "query": query,
            "mode": backend_mode,
            "model": selected_model,
            "api_key_detected": bool(effective_api_key),
            "status": answer.status,
            "route_decision": answer_payload.get("route_decision") or {},
            "privacy_guardrail": self._privacy_guardrail(answer_payload),
            "intent_decomposition": self._read_json(run_dir / "intent_decomposition.json"),
            "execution_brief": self._read_json(run_dir / "smartspace_execution_brief.json"),
            "tracefix_task_spec": task_spec,
            "tracefix_bundle": self._read_json(run_dir / "tracefix_bundle" / "bundle.json"),
            "answer_packet": answer_payload,
            "run_dir": str(run_dir),
            "warnings": warnings,
        }
        state = {
            "run_id": answer.query_id,
            "tellme": data,
            "tracefix": {},
            "cityos": {},
        }
        self._write_json(self.current_path, state)
        return data

    @staticmethod
    def llm_config() -> dict[str, Any]:
        config = get_llm_config()
        openai_key = (os.getenv("OPENAI_API_KEY") or "").strip()
        tellme_key = (os.getenv("TELLME_API_KEY") or "").strip()
        api_key_detected = bool(openai_key or tellme_key)
        return {
            "mode": "deterministic",
            "model": config.model or DEFAULT_OPENAI_MODEL,
            "api_key_detected": api_key_detected,
            "openai_api_key_detected": bool(openai_key),
            "tellme_api_key_detected": bool(tellme_key),
            "supported_modes": ["deterministic", "llm"],
            "key_environment_variables": ["TELLME_API_KEY", "OPENAI_API_KEY"],
            "model_environment_variable": "TELLME_MODEL",
        }

    def current(self) -> dict[str, Any] | None:
        payload = self._read_json(self.current_path)
        return payload if payload else None

    def tracefix_task_text(self) -> str:
        current = self.current()
        if not current:
            raise ValueError("No TeLLMe run is available. Process a request in the TeLLMe tab first.")
        tellme = current.get("tellme") or {}
        task_spec = tellme.get("tracefix_task_spec")
        if not isinstance(task_spec, dict) or not task_spec:
            raise ValueError("The current TeLLMe run did not produce a TraceFix-compatible task spec.")
        if tellme.get("status") not in {"needs_tracefix", "answered"}:
            raise ValueError(
                f"The current TeLLMe run is not eligible for TraceFix design: {tellme.get('status') or 'unknown'}."
            )

        return "\n".join(
            [
                "TeLLMe structured smart-room application requirements.",
                "Treat this as compile-time input to TraceFix planning and verification.",
                "Generate a complete multi-agent topology with explicit communication channels.",
                "Do not execute production agents or bypass PlusCal/TLC verification.",
                "",
                "Structured task specification:",
                json.dumps(task_spec, indent=2, ensure_ascii=False),
            ]
        )

    def record_tracefix_run(self, run_id: str) -> dict[str, Any]:
        state = self.current()
        if not state:
            raise ValueError("No TeLLMe run is available.")
        state["tracefix"] = {"run_id": run_id}
        self._write_json(self.current_path, state)
        return state

    def record_tracefix_workspace(self, run_id: str, workspace: str) -> None:
        state = self.current()
        if not state:
            return
        tracefix = state.setdefault("tracefix", {})
        if tracefix.get("run_id") != run_id:
            return
        tracefix["workspace"] = workspace
        self._write_json(self.current_path, state)

    def record_cityos_result(self, result: dict[str, Any]) -> None:
        state = self.current()
        if not state:
            return
        state["cityos"] = result
        self._write_json(self.current_path, state)

    def artifact_paths(self, data: dict[str, Any] | None = None) -> list[str]:
        current_data = data or ((self.current() or {}).get("tellme") or {})
        run_dir_raw = str(current_data.get("run_dir") or "")
        if not run_dir_raw:
            return []
        run_dir = Path(run_dir_raw)
        candidates = (
            run_dir / "route_decision.json",
            run_dir / "intent_decomposition.json",
            run_dir / "smartspace_execution_brief.json",
            run_dir / "tracefix_task_spec.json",
            run_dir / "answer_packet.json",
            run_dir / "tracefix_bundle" / "bundle.json",
        )
        return [str(path) for path in candidates if path.exists()]

    @staticmethod
    def _privacy_guardrail(answer: dict[str, Any]) -> dict[str, Any]:
        route = answer.get("route_decision") or {}
        task_spec = answer.get("tracefix_task_spec") or {}
        policy = task_spec.get("privacy_policy") if isinstance(task_spec, dict) else {}
        blocked = route.get("route") == "not_allowed"
        return {
            "status": "blocked" if blocked else "passed",
            "privacy_scope": answer.get("privacy_scope") or "cityos_structured_context_only",
            "raw_sensor_access_allowed": bool(policy.get("raw_sensor_access_allowed", False))
            if isinstance(policy, dict)
            else False,
            "identity_inference_allowed": bool(policy.get("identity_inference_allowed", False))
            if isinstance(policy, dict)
            else False,
            "caveats": answer.get("caveats") or [],
        }

    @staticmethod
    def _read_json(path: Path) -> dict[str, Any]:
        if not path.exists():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    @staticmethod
    def _write_json(path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)
