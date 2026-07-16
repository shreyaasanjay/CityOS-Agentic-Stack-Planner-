"""Canonical coordination-pattern vocabulary.

These names describe communication/coordination patterns between agents.  They
are shared by the LLM attribute extractor and protocol template data objects.
"""
from __future__ import annotations

from collections.abc import Iterable

COORDINATION_PATTERNS: tuple[str, ...] = (
    "Subscription",
    "Checkpoint",
    "Cross Validation",
    "Cascade",
    "Backup",
    "Split-and-Merge",
    "Fair Judgement",
    "Interruption Recovery",
    "Confirmation Loop",
    "Frozen Resource",
    "Priority Escort",
    "Progressive Disclosure",
    "Observer",
    "Dynamic Pairing",
    "Token Passing",
    "Opportunity Window",
    "Leader-Follower",
    "Isolate Agent",
    "Consensus",
    "Task Prioritization",
    "Verification",
    "Checkpoint Recovery",
    "Adaptive Yield",
    "Courtesy Yield",
    "Role Switching",
    "Replication",
    "Request-Grant",
    "Producer-Consumer",
    "Barrier Synchronization",
    "Sequential Handoff",
    "Broadcast",
    "Election",
    "Heartbeat Monitoring",
    "Request-Response",
    "Reservation",
    "Queue-Based Scheduling",
    "Exclusive Resource Access",
    "Majority Voting",
    "Retry with Timeout",
    "Publish-Subscribe",
)

_CANONICAL_BY_CASEFOLD = {pattern.casefold(): pattern for pattern in COORDINATION_PATTERNS}


def normalize_coordination_pattern(value: str) -> str:
    """Return canonical pattern spelling, or raise ValueError."""

    key = str(value or "").strip().casefold()
    if key not in _CANONICAL_BY_CASEFOLD:
        raise ValueError(f"unknown coordination pattern: {value!r}")
    return _CANONICAL_BY_CASEFOLD[key]


def normalize_coordination_patterns(values: Iterable[str]) -> list[str]:
    """Normalize and deduplicate coordination patterns in first-seen order."""

    if values is None:
        return []
    normalized: list[str] = []
    seen: set[str] = set()
    for value in values:
        canonical = normalize_coordination_pattern(value)
        if canonical not in seen:
            seen.add(canonical)
            normalized.append(canonical)
    return normalized


def is_valid_coordination_pattern(value: str) -> bool:
    """Return True when ``value`` is a known coordination pattern."""

    try:
        normalize_coordination_pattern(value)
    except ValueError:
        return False
    return True
