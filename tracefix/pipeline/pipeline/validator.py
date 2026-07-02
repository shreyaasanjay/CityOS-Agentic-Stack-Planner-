"""IR v3 validator: schema validation + semantic checks (PlusCal pipeline).

Validates agents, resources, and channels only. Agent behavior is written
as PlusCal process bodies, not JSON states.
"""

from copy import deepcopy
from dataclasses import dataclass, field
from collections.abc import Iterable
from pathlib import Path
import json
import re

import jsonschema

from tracefix.textio import safe_read_json


@dataclass
class ValidationResult:
    valid: bool
    errors: list[str] = field(default_factory=list)


_SCHEMA_PATH = Path(__file__).parent / "schema.json"
_SCHEMA = None
_HARMLESS_METADATA_FIELDS = frozenset({
    "comments",
    "description",
    "explanation",
    "metadata",
    "notes",
    "rationale",
    "role",
})
_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _get_schema() -> dict:
    global _SCHEMA
    if _SCHEMA is None:
        _SCHEMA = safe_read_json(_SCHEMA_PATH, {})
    return _SCHEMA


def _normalize_list(val) -> list[str]:
    """Normalize a string-or-list field to a list."""
    if isinstance(val, str):
        return [val]
    return list(val)


def _json_path(parts: Iterable) -> str:
    path = "$"
    for part in parts:
        if isinstance(part, int):
            path += f"[{part}]"
        else:
            path += f".{part}"
    return path


def _legacy_resource_entries(value, *, kind: str, path: str) -> tuple[list[dict], list[str]]:
    resources: list[dict] = []
    diagnostics: list[str] = []
    resource_type = "Lock" if kind == "locks" else "Counter"

    if isinstance(value, dict):
        iterable = list(value.items())
    elif isinstance(value, list):
        iterable = list(enumerate(value))
    else:
        diagnostics.append(
            f"IR normalized: ignored legacy field {path}; expected list/object"
        )
        return resources, diagnostics

    for key, entry in iterable:
        entry_path = f"{path}.{key}" if isinstance(value, dict) else f"{path}[{key}]"
        if isinstance(entry, str):
            resource = {"id": entry, "type": resource_type}
        elif isinstance(entry, dict):
            rid = entry.get("id") or entry.get("name") or (key if isinstance(key, str) else None)
            if not rid:
                diagnostics.append(
                    f"IR normalized: ignored legacy field {entry_path}; missing id"
                )
                continue
            resource = {"id": str(rid), "type": resource_type}
            if resource_type == "Counter":
                if isinstance(entry.get("config"), dict):
                    resource["config"] = deepcopy(entry["config"])
                elif "initial" in entry:
                    resource["config"] = {"initial": entry["initial"]}
        elif isinstance(key, str):
            resource = {"id": key, "type": resource_type}
            if resource_type == "Counter" and isinstance(entry, int):
                resource["config"] = {"initial": entry}
        else:
            diagnostics.append(
                f"IR normalized: ignored legacy field {entry_path}; expected string/object"
            )
            continue
        resources.append(resource)
        diagnostics.append(
            f"IR normalized: moved legacy field {entry_path} to $.resources as {resource_type}"
        )

    return resources, diagnostics


def normalize_ir_with_diagnostics(ir_data: dict) -> tuple[dict, list[str]]:
    """Return a scaffold-friendly copy of an IR-like object.

    The planner sometimes carries benchmark metadata through the IR boundary
    (string agents/resources, legacy top-level locks/counters,
    agent_resources, tool_resource_map). The TLA+ scaffold expects object
    agents/resources and explicit communication channels. This function keeps
    allowed metadata intact while normalizing the verified topology fields.
    """
    normalized = deepcopy(ir_data)
    diagnostics: list[str] = []

    agents = []
    for index, agent in enumerate(normalized.get("agents", [])):
        if isinstance(agent, str):
            agents.append({"id": agent})
            diagnostics.append(
                f"IR normalized: moved legacy field $.agents[{index}] to object id"
            )
        else:
            agents.append(agent)
    normalized["agents"] = agents

    resources = []
    for index, resource in enumerate(normalized.get("resources", [])):
        if isinstance(resource, str):
            resources.append({"id": resource, "type": "Lock"})
            diagnostics.append(
                f"IR normalized: moved legacy field $.resources[{index}] to Lock resource"
            )
        else:
            resources.append(resource)

    existing_resource_ids = {r["id"] for r in resources if isinstance(r, dict) and "id" in r}
    for legacy_key in ("locks", "counters"):
        if legacy_key in normalized:
            legacy_resources, legacy_diagnostics = _legacy_resource_entries(
                normalized.pop(legacy_key),
                kind=legacy_key,
                path=f"$.{legacy_key}",
            )
            for res, diag in zip(legacy_resources, legacy_diagnostics):
                if res.get("id") in existing_resource_ids:
                    diagnostics.append(
                        f"IR normalized: skipped duplicate legacy resource {res['id']} "
                        f"(already in $.resources)"
                    )
                else:
                    resources.append(res)
                    existing_resource_ids.add(res["id"])
                    diagnostics.append(diag)

    normalized["resources"] = resources

    return normalized, diagnostics


def normalize_ir(ir_data: dict) -> dict:
    """Return the canonical schema-facing form of an IR-like object."""
    return canonicalize_ir_with_diagnostics(ir_data)[0]


def sanitize_ir_with_diagnostics(ir_data: dict) -> tuple[dict, dict]:
    """Conservatively remove non-semantic model noise from an IR candidate.

    This does not invent agents, resources, channels, or references. The
    returned candidate must still pass ``validate_ir`` before callers persist
    or scaffold it.
    """
    sanitized = deepcopy(ir_data)
    removed_fields: list[str] = []
    normalized_fields: list[str] = []

    def _strip_metadata(value, path: str):
        if isinstance(value, dict):
            cleaned = {}
            for key, child in value.items():
                child_path = f"{path}.{key}"
                if key in _HARMLESS_METADATA_FIELDS:
                    removed_fields.append(child_path)
                    continue
                cleaned[key] = _strip_metadata(child, child_path)
            return cleaned
        if isinstance(value, list):
            return [
                _strip_metadata(child, f"{path}[{index}]")
                for index, child in enumerate(value)
            ]
        return value

    sanitized = _strip_metadata(sanitized, "$")

    channels = sanitized.get("channels")
    if isinstance(channels, list):
        unique_channels = []
        seen_channels: dict[str, int] = {}
        for index, channel in enumerate(channels):
            if not isinstance(channel, dict):
                unique_channels.append(channel)
                continue
            fingerprint = json.dumps(channel, sort_keys=True, separators=(",", ":"))
            if fingerprint in seen_channels:
                normalized_fields.append(
                    f"$.channels[{index}] removed as identical duplicate of "
                    f"$.channels[{seen_channels[fingerprint]}]"
                )
                continue
            seen_channels[fingerprint] = index
            unique_channels.append(channel)
        sanitized["channels"] = unique_channels

        for index, channel in enumerate(unique_channels):
            if not isinstance(channel, dict):
                continue
            labels = channel.get("labels")
            if not isinstance(labels, list) or not all(
                isinstance(label, str) for label in labels
            ):
                continue
            replacements = {
                label: re.sub(r"\s+", "_", label.strip())
                for label in labels
            }
            if not any(replacements[label] != label for label in labels):
                continue
            replacement_values = list(replacements.values())
            if (
                any(not value or not _SAFE_IDENTIFIER.fullmatch(value)
                    for value in replacement_values)
                or len(set(replacement_values)) != len(replacement_values)
            ):
                continue
            channel["labels"] = [replacements[label] for label in labels]
            content_labels = channel.get("content_labels")
            if isinstance(content_labels, list):
                channel["content_labels"] = [
                    replacements.get(label, label) for label in content_labels
                ]
            normalized_fields.append(
                f"$.channels[{index}].labels normalized to safe identifiers"
            )

    report = {
        "attempted": True,
        "changed": sanitized != ir_data,
        "removed_fields": removed_fields,
        "normalized_fields": normalized_fields,
    }
    return sanitized, report


def canonicalize_ir_with_diagnostics(ir_data: dict) -> tuple[dict, dict]:
    """Canonicalize an IR candidate without inventing protocol structure.

    The schema remains the source of truth. This boundary only converts
    supported legacy representations and removes explicitly approved
    non-verification metadata before strict validation.
    """
    normalized, legacy_diagnostics = normalize_ir_with_diagnostics(ir_data)
    canonical, sanitizer = sanitize_ir_with_diagnostics(normalized)
    diagnostics = list(legacy_diagnostics)
    diagnostics.extend(
        f"IR canonicalized: removed metadata field {path}"
        for path in sanitizer["removed_fields"]
    )
    diagnostics.extend(
        f"IR canonicalized: {change}"
        for change in sanitizer["normalized_fields"]
    )
    return canonical, {
        "attempted": True,
        "changed": canonical != ir_data,
        "legacy_normalizations": legacy_diagnostics,
        "removed_fields": list(sanitizer["removed_fields"]),
        "normalized_fields": list(sanitizer["normalized_fields"]),
        "diagnostics": diagnostics,
    }


def _agent_id_to_const(agent_id: str) -> str:
    """Convert agent ID to PlusCal CONSTANT name (must match pluscal_generator)."""
    s = agent_id.replace("-", "_").replace(" ", "_")
    if not s:
        return "Agent"
    return s[0].upper() + s[1:]


def validate_canonical_ir(ir_data: dict) -> ValidationResult:
    """Strictly validate canonical IR v3 data against schema and semantics.

    Checks agents, resources, and channels. Does NOT validate states
    (behavior is written as PlusCal process bodies).
    """
    errors: list[str] = []

    # --- Schema validation ---
    schema = _get_schema()
    validator = jsonschema.Draft7Validator(schema)
    for error in validator.iter_errors(ir_data):
        path = _json_path(error.absolute_path)
        if error.validator == "additionalProperties" and isinstance(error.instance, dict):
            allowed = set((error.schema or {}).get("properties", {}))
            illegal = sorted(set(error.instance) - allowed)
            if illegal:
                for field in illegal:
                    errors.append(
                        f"Schema: Illegal field at {path}.{field}: not allowed by IR schema"
                    )
                continue
        errors.append(f"Schema: {error.message} (at {path})")

    if errors:
        return ValidationResult(valid=False, errors=errors)

    # --- Semantic validation ---
    agent_ids = set()
    agent_consts: dict[str, str] = {}  # const_name -> agent_id (for collision check)
    for agent in ir_data.get("agents", []):
        aid = agent["id"]
        if aid in agent_ids:
            errors.append(f"Duplicate agent ID: {aid}")
        agent_ids.add(aid)

        # Check for constant name collisions
        const_name = _agent_id_to_const(aid)
        if const_name in agent_consts:
            errors.append(
                f"Agent '{aid}' and '{agent_consts[const_name]}' produce the same "
                f"TLA+ constant name '{const_name}'. Use more distinct agent IDs."
            )
        else:
            agent_consts[const_name] = aid

    resource_ids = set()
    for res in ir_data.get("resources", []):
        rid = res["id"]
        if rid in resource_ids:
            errors.append(f"Duplicate resource ID: {rid}")
        resource_ids.add(rid)
        if res["type"] == "Counter":
            config = res.get("config")
            if not config or "initial" not in config:
                errors.append(f"Counter resource '{rid}' must have config.initial defined")

    channel_ids = set()
    # Track (sender, receiver) pairs to enforce one channel per directed pair
    directed_pairs: dict[tuple[str, str], str] = {}
    for ch in ir_data.get("channels", []):
        cid = ch["id"]
        if cid in channel_ids:
            errors.append(f"Duplicate channel ID: {cid}")
        channel_ids.add(cid)
        ch_from = _normalize_list(ch["from"])
        ch_to = _normalize_list(ch["to"])
        for a in ch_from:
            if a not in agent_ids:
                errors.append(f"Channel '{cid}' from references unknown agent: {a}")
        for a in ch_to:
            if a not in agent_ids:
                errors.append(f"Channel '{cid}' to references unknown agent: {a}")
        # content_labels (optional, data-plane) must be a subset of labels
        bad_cl = [lbl for lbl in ch.get("content_labels", [])
                  if lbl not in ch.get("labels", [])]
        if bad_cl:
            errors.append(
                f"Channel '{cid}': content_labels {bad_cl} not in labels "
                f"{ch.get('labels', [])}")
        # Check duplicate (from, to) pairs
        for sender in ch_from:
            for receiver in ch_to:
                pair = (sender, receiver)
                if pair in directed_pairs:
                    errors.append(
                        f"Channel '{cid}': agents {sender}\u2192{receiver} already connected "
                        f"by channel '{directed_pairs[pair]}'. Use one channel per "
                        f"directed pair and distinguish messages with labels."
                    )
                else:
                    directed_pairs[pair] = cid

    if len(agent_ids) > 1 and not channel_ids:
        errors.append(
            "IR incomplete: no communication channels generated. PlusCal/TLC cannot run."
        )

    return ValidationResult(valid=len(errors) == 0, errors=errors)


def validate_ir(ir_data: dict) -> ValidationResult:
    """Canonicalize an IR candidate, then apply strict IR v3 validation."""
    return validate_canonical_ir(normalize_ir(ir_data))
