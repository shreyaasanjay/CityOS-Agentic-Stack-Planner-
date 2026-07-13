"""Service-local audit helpers for sanitized provenance and state hashing."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

POLICY_VERSION = "capability_service_phase4_v1"
AUDIT_LOG_FILENAME = "capability_service_audit.jsonl"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_event_id() -> str:
    return f"audit_{uuid4().hex[:12]}"


def stable_json_dumps(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def opaque_state_hash(payload: Any) -> str:
    return hashlib.sha256(stable_json_dumps(payload).encode("utf-8")).hexdigest()
