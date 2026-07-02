"""Parameterized N-agent evidence fan-in and reconciliation protocol."""
from __future__ import annotations

import re

from tracefix.pipeline.pipeline.pluscal_generator import (
    _agent_id_to_const,
    _sanitize_id,
)

PATTERN_ID = "fan_in_decision"
DESCRIPTION = (
    "Three or more independent evidence agents each send one result to a "
    "decision/reconciliation agent, which waits for every source before deciding."
)

_EVIDENCE_SOURCES = (
    ("observed room occupancy", "occupancy", "observed room occupancy"),
    ("room occupancy", "occupancy", "room occupancy"),
    ("expected attendance records", "attendance_records", "expected attendance records"),
    ("attendance records", "attendance_records", "attendance records"),
    ("badge check-in status", "badge_check_in", "badge check-in status"),
    ("badge check-in", "badge_check_in", "badge check-in"),
    ("calendar participation updates", "calendar_updates", "calendar participation updates"),
    ("calendar participation", "calendar_updates", "calendar participation"),
    ("equipment readiness", "equipment_readiness", "equipment readiness"),
    ("sensor context", "sensor_context", "sensor context"),
    ("structured context", "structured_context", "structured context"),
    ("safety inspection status", "safety_inspection", "safety inspection status"),
    ("safety inspection", "safety_inspection", "safety inspection"),
    ("maintenance status", "maintenance_status", "maintenance status"),
    ("access logs", "access_logs", "access logs"),
    ("attendance updates", "attendance_updates", "attendance updates"),
)

_DECISION_TERMS = (
    "independently evaluating",
    "reconcile conflicting evidence",
    "reconcile",
    "resolve discrepancies",
    "identify unresolved issues",
    "compare sources",
    "evidence quality",
    "final decision",
    "readiness decision",
    "validation decision",
)


def detect_evidence_sources(task_lower: str) -> list[dict[str, str]]:
    """Return ordered, de-duplicated evidence sources explicitly named in a task."""
    text = task_lower.lower()
    detected: list[dict[str, str]] = []
    seen: set[str] = set()
    for phrase, source_id, display_name in _EVIDENCE_SOURCES:
        if phrase not in text or source_id in seen:
            continue
        seen.add(source_id)
        detected.append({
            "id": f"{source_id}_agent",
            "name": display_name,
            "label": f"{source_id}_evidence",
        })
    return detected


def has_decision_language(task_lower: str) -> bool:
    return any(term in task_lower.lower() for term in _DECISION_TERMS)


def classify(task_lower: str, agent_count_hint: int, keywords: frozenset[str]) -> float:
    del agent_count_hint, keywords
    sources = detect_evidence_sources(task_lower)
    if len(sources) < 3 or not has_decision_language(task_lower):
        return 0.0
    return 0.98


def build_template(params: dict) -> tuple[dict, str]:
    sources = list(params.get("evidence_sources") or [])
    if len(sources) < 3:
        raise ValueError("fan_in_decision requires at least three evidence sources")

    decision_id = _safe_id(params.get("decision_agent_id") or "decision_reconciliation")
    channel_bound = int(params.get("channel_bound", 3))
    source_rows: list[dict[str, str]] = []
    seen_ids: set[str] = {decision_id}
    for index, source in enumerate(sources):
        raw_id = source.get("id") if isinstance(source, dict) else str(source)
        source_id = _safe_id(raw_id or f"evidence_source_{index + 1}")
        if source_id in seen_ids:
            source_id = f"{source_id}_{index + 1}"
        seen_ids.add(source_id)
        display = (
            str(source.get("name") or source_id)
            if isinstance(source, dict)
            else source_id
        )
        label = _safe_id(
            (source.get("label") or f"evidence_{index + 1}")
            if isinstance(source, dict)
            else f"evidence_{index + 1}"
        )
        source_rows.append({"id": source_id, "name": display, "label": label})

    agents = [{"id": row["id"]} for row in source_rows] + [{"id": decision_id}]
    channels = [
        {
            "id": f"{row['id']}_to_{decision_id}",
            "from": row["id"],
            "to": decision_id,
            "labels": [row["label"]],
        }
        for row in source_rows
    ]
    state_tasks = {
        f"{row['id']}_start": f"Evaluate {row['name']} independently."
        for row in source_rows
    }
    state_tasks[f"{decision_id}_reconcile"] = (
        "Reconcile all evidence, identify unresolved issues, and produce the final decision."
    )
    ir_data = {
        "agents": agents,
        "resources": [],
        "channels": channels,
        "state_tasks": state_tasks,
    }

    agent_consts = {
        agent["id"]: _agent_id_to_const(agent["id"])
        for agent in agents
    }
    all_consts = "{" + ", ".join(agent_consts.values()) + "}"
    channel_vars = {
        channel["id"]: _sanitize_id(channel["id"])
        for channel in channels
    }

    lines = [
        "---- MODULE Protocol ----",
        "EXTENDS Integers, Sequences, TLC",
        "",
        "CONSTANTS " + ", ".join(agent_consts.values()),
        "",
        "(* --algorithm Protocol {",
        "variables",
    ]
    for channel in channels:
        lines.append(
            f"  {channel_vars[channel['id']]} = <<>>; "
            f"\\* {channel['from']} -> {decision_id}"
        )
    lines.extend([
        "",
        "macro send(ch, msg) {",
        "  ch := Append(ch, msg);",
        "}",
        "",
        "macro receive(ch, var) {",
        "  await Len(ch) > 0;",
        "  var := Head(ch);",
        "  ch := Tail(ch);",
        "}",
        "",
    ])

    for row, channel in zip(source_rows, channels):
        source_var = _sanitize_id(row["id"])
        channel_var = channel_vars[channel["id"]]
        lines.extend([
            f"fair process ({source_var}_proc \\in {{{agent_consts[row['id']]}}})",
            'variables msg = "";',
            "{",
            f"  {source_var}_start:",
            f"    skip; \\* independently evaluate {row['name']}",
            f"  {source_var}_send:",
            f'    send({channel_var}, "{row["label"]}");',
            f"  {source_var}_done:",
            "    skip;",
            "}",
            "",
        ])

    decision_var = _sanitize_id(decision_id)
    lines.extend([
        f"fair process ({decision_var}_proc \\in {{{agent_consts[decision_id]}}})",
        'variables msg = "";',
        "{",
    ])
    for index, channel in enumerate(channels):
        label = "start" if index == 0 else f"receive_{index + 1}"
        lines.extend([
            f"  {decision_var}_{label}:",
            f"    receive({channel_vars[channel['id']]}, msg);",
        ])
    lines.extend([
        f"  {decision_var}_reconcile:",
        "    skip; \\* application-level reconciliation and final decision",
        f"  {decision_var}_done:",
        "    skip;",
        "}",
        "",
        "} *)",
        "",
        f'AllDone == \\A p \\in {all_consts}: pc[p] = "Done"',
        "",
        "TypeInvariant ==",
        f"  /\\ \\A p \\in {all_consts}: pc[p] \\in STRING",
    ])
    lines.extend(
        f"  /\\ {channel_var} \\in Seq(STRING)"
        for channel_var in channel_vars.values()
    )
    drained = " /\\ ".join(
        f"Len({channel_var}) = 0" for channel_var in channel_vars.values()
    )
    bounded = " /\\ ".join(
        f"Len({channel_var}) <= {channel_bound}"
        for channel_var in channel_vars.values()
    )
    lines.extend([
        "",
        "ChannelsDrained ==",
        f"  AllDone => ({drained})",
        "",
        "ChannelBound ==",
        f"  {bounded}",
        "",
        "====",
    ])
    return ir_data, "\n".join(lines)


def _safe_id(value: object) -> str:
    normalized = re.sub(r"[^a-z0-9_]", "_", str(value).lower().strip()).strip("_")
    if not normalized:
        return "agent"
    if normalized[0].isdigit():
        normalized = f"agent_{normalized}"
    return normalized
