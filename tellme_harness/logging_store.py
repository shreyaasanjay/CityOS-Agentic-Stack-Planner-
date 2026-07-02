"""Local JSON/JSONL logging store for TeLLMe Harness V0."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any


class LocalLoggingStore:
    def __init__(self, runs_root: str | Path | None = None) -> None:
        base_dir = Path(__file__).resolve().parent.parent
        self.runs_root = Path(runs_root) if runs_root else base_dir / ".runs" / "tellme"
        self.fallback_active: bool = False
        self.original_runs_root: Path | None = None
        self._last_query_dir: Path | None = None

    def ensure_query_dir(self, query_id: str) -> Path:
        query_dir = self.runs_root / query_id
        try:
            query_dir.mkdir(parents=True, exist_ok=True)
        except PermissionError:
            if not self.fallback_active:
                self.original_runs_root = self.runs_root
                self.fallback_active = True
            self.runs_root = Path(tempfile.gettempdir()) / "tellme" / "runs"
            query_dir = self.runs_root / query_id
            query_dir.mkdir(parents=True, exist_ok=True)
        self._last_query_dir = query_dir
        return query_dir

    def get_query_run_info(self, query_id: str) -> dict[str, Any]:
        query_dir = self.ensure_query_dir(query_id)
        info: dict[str, Any] = {
            "run_dir": str(query_dir),
            "logging_fallback_used": self.fallback_active,
        }
        if self.fallback_active:
            info["original_runs_root"] = str(self.original_runs_root)
            info["fallback_runs_root"] = str(self.runs_root)
        return info

    def write_json(self, query_id: str, filename: str, payload: Any) -> Path:
        query_dir = self.ensure_query_dir(query_id)
        output_path = query_dir / filename
        serializable = payload.model_dump() if hasattr(payload, "model_dump") else payload
        self._atomic_write(output_path, json.dumps(serializable, indent=2, sort_keys=True) + "\n")
        return output_path

    def write_text(self, query_id: str, filename: str, content: str) -> Path:
        query_dir = self.ensure_query_dir(query_id)
        output_path = query_dir / filename
        self._atomic_write(output_path, content if content.endswith("\n") else content + "\n")
        return output_path

    def write_json_in_subdir(self, query_id: str, subdir: str, filename: str, payload: Any) -> Path:
        query_dir = self.ensure_query_dir(query_id)
        output_dir = query_dir / subdir
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / filename
        serializable = payload.model_dump() if hasattr(payload, "model_dump") else payload
        self._atomic_write(output_path, json.dumps(serializable, indent=2, sort_keys=True) + "\n")
        return output_path

    def write_text_in_subdir(self, query_id: str, subdir: str, filename: str, content: str) -> Path:
        query_dir = self.ensure_query_dir(query_id)
        output_dir = query_dir / subdir
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / filename
        self._atomic_write(output_path, content if content.endswith("\n") else content + "\n")
        return output_path

    def append_event(self, query_id: str, event: dict[str, Any]) -> Path:
        query_dir = self.ensure_query_dir(query_id)
        output_path = query_dir / "events.jsonl"
        line = json.dumps(event, sort_keys=True) + "\n"
        with output_path.open("a", encoding="utf-8") as handle:
            handle.write(line)
            handle.flush()
        return output_path

    def _atomic_write(self, output_path: Path, content: str) -> None:
        tmp_path = output_path.with_suffix(output_path.suffix + ".tmp")
        with tmp_path.open("w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
        tmp_path.replace(output_path)
