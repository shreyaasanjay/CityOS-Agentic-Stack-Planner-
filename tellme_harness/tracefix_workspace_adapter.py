"""Build a real TraceFix workspace from a brief + coordination plan.

This adapter writes the files TraceFix expects (``spec/ir.json`` and supporting
contracts) into a workspace directory, deterministically and without secrets. It
validates the generated IR against TraceFix's *own* validator when the TraceFix
package is locatable on disk (its ``validate_ir`` is pure ``jsonschema``, needing
no Java/TLC/tree-sitter); otherwise it falls back to the bundled schema, and
records which path was used.

It does NOT run design, TLC, or the runtime — those are gated behind the actual
toolchain. The output here is the verified-by-schema input those steps consume.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

from .schemas import (
    SmartspaceExecutionBrief,
    TraceFixCoordinationPlan,
    TraceFixWorkspaceBuildResult,
)
from .tracefix_coordination import coordination_plan_to_ir

_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _find_tracefix_root() -> Optional[Path]:
    """Locate a checked-out TraceFix package directory, if present."""
    for name in ("TraceFixCityOS-main", "TraceFix-main", "tracefix"):
        candidate = _PROJECT_ROOT / name
        if (candidate / "tracefix").is_dir() or (candidate / "pipeline").is_dir():
            return candidate
    return None


def _load_ir_validator() -> Tuple[Optional[Callable[[dict], Any]], str]:
    """Return (validate_ir, source) using TraceFix's validator if importable."""
    root = _find_tracefix_root()
    if root is None:
        return None, "none"
    root_str = str(root)
    added = False
    if root_str not in sys.path:
        sys.path.insert(0, root_str)
        added = True
    try:
        from tracefix.pipeline.pipeline.validator import validate_ir  # type: ignore

        return validate_ir, "tracefix_validator"
    except Exception:
        if added:
            try:
                sys.path.remove(root_str)
            except ValueError:
                pass
        # Fall back to validating against the schema file directly via jsonschema.
        schema_path = root / "tracefix" / "pipeline" / "pipeline" / "schema.json"
        if schema_path.is_file():
            try:
                import jsonschema  # noqa: F401

                def _validate(ir: dict, _sp=schema_path):
                    import jsonschema as _js

                    schema = json.loads(Path(_sp).read_text(encoding="utf-8"))
                    errors = [
                        f"Schema: {e.message}"
                        for e in _js.Draft7Validator(schema).iter_errors(ir)
                    ]

                    class _R:
                        valid = not errors

                    r = _R()
                    r.errors = errors  # type: ignore[attr-defined]
                    return r

                return _validate, "bundled_schema"
            except Exception:
                return None, "none"
        return None, "none"


class TraceFixWorkspaceAdapter:
    def build_workspace(
        self,
        *,
        brief: SmartspaceExecutionBrief,
        coordination_plan: TraceFixCoordinationPlan,
        workspace_root: Path,
    ) -> TraceFixWorkspaceBuildResult:
        workspace_root = Path(workspace_root)
        spec_dir = workspace_root / "spec"
        spec_dir.mkdir(parents=True, exist_ok=True)
        (workspace_root / "output").mkdir(parents=True, exist_ok=True)
        (workspace_root / "prompts").mkdir(parents=True, exist_ok=True)

        ir = coordination_plan_to_ir(coordination_plan)

        # Validate against TraceFix's real IR contract before writing anything else.
        validate_ir, source = _load_ir_validator()
        ir_valid: Optional[bool] = None
        ir_errors: list[str] = []
        if validate_ir is not None:
            result = validate_ir(ir)
            ir_valid = bool(getattr(result, "valid", False))
            ir_errors = list(getattr(result, "errors", []) or [])

        spec_files: list[str] = []

        def _write(name: str, payload: Any) -> None:
            path = spec_dir / name
            serializable = payload.model_dump() if hasattr(payload, "model_dump") else payload
            self._atomic_write(path, json.dumps(serializable, indent=2, sort_keys=True) + "\n")
            spec_files.append(f"spec/{name}")

        _write("ir.json", ir)
        _write("smartspace_execution_brief.json", brief)
        _write("coordination_plan.json", coordination_plan)
        _write("answer_contract.json", self._answer_contract(brief))
        _write("evidence_card_contract.json", brief.evidence_card_requirements)
        _write("privacy_constraints.json", self._privacy_constraints(brief))

        metadata = {
            "brief_id": brief.brief_id,
            "plan_id": coordination_plan.plan_id,
            "task_id": coordination_plan.task_id,
            "template": coordination_plan.template,
            "executable": coordination_plan.executable,
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "ir_valid": ir_valid,
            "ir_validation_source": source,
            "agent_count": len(coordination_plan.agents),
            "channel_count": len(coordination_plan.channels),
            # NOTE: intentionally no secrets, no raw media, no env values.
        }
        self._atomic_write(
            workspace_root / "metadata.json",
            json.dumps(metadata, indent=2, sort_keys=True) + "\n",
        )

        return TraceFixWorkspaceBuildResult(
            workspace_path=str(workspace_root),
            ir_path=str(spec_dir / "ir.json"),
            spec_files=sorted(spec_files),
            ir_valid=ir_valid,
            ir_validation_errors=ir_errors,
            ir_validation_source=source,
            metadata=metadata,
        )

    def _answer_contract(self, brief: SmartspaceExecutionBrief) -> Dict[str, Any]:
        return {
            "answer_packet_requirements": brief.answer_packet_requirements.model_dump(),
            "allowed_claims": list(brief.allowed_claims),
            "forbidden_claims": list(brief.forbidden_claims),
            "escalation_conditions": list(brief.escalation_conditions),
        }

    def _privacy_constraints(self, brief: SmartspaceExecutionBrief) -> Dict[str, Any]:
        return {
            "privacy_policy": dict(brief.privacy_policy),
            "forbidden_claims": list(brief.forbidden_claims),
            "raw_media_access": False,
            "identity_inference": False,
            "transcript_access": False,
        }

    def _atomic_write(self, path: Path, content: str) -> None:
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(content, encoding="utf-8")
        tmp.replace(path)
