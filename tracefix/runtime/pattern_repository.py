"""Verified Pattern Repository.

After a multi-agent workflow passes IR validation + PlusCal/TLC verification
via OpenCode (or any non-static-template path), its normalized coordination
structure is saved here as a *candidate*.

IMPORTANT: Candidates are NOT active templates.
  - They must be manually reviewed before promotion.
  - Saving a candidate never bypasses PlusCal or TLC.
  - Existing static templates in tracefix/protocol_templates/ are NOT replaced.
  - Candidates do not feed back into the coordination classifier automatically.

Environment flags
-----------------
TRACEFIX_PATTERN_REPOSITORY_ENABLED  default "true"
TRACEFIX_HARVEST_SINGLE_AGENT        default "false"
TRACEFIX_PATTERN_REPOSITORY_DIR      override candidate store root (default:
                                     <repo_root>/tracefix/protocol_candidates)
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sys
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_WRITE_LOCK = threading.Lock()

_README_BODY = """\
# Pattern Candidate

This candidate is **not an active protocol template**.
It must be reviewed and promoted manually before use.

The files in this folder were captured from a verified TraceFix run:

- `candidate_metadata.json` — provenance, counts, promotion status
- `normalized_topology.json` — agent/channel/resource structure (names anonymized)
- `source_ir.json` — the original IR from the verified run
- `Protocol.tla` — the PlusCal/TLA+ that passed TLC
- `Protocol.cfg` — TLC configuration (if captured)
- `pipeline_timing_report.json` — timing diagnostics (if captured)

## Promotion checklist

- [ ] Review normalized topology for correctness
- [ ] Verify no task-specific logic is encoded in Protocol.tla
- [ ] Write a new module in `tracefix/protocol_templates/` based on this candidate
- [ ] Add tests in `tracefix/runtime/opencode_adapter/tests/`
- [ ] Update `tracefix/protocol_templates/__init__.py` registry
- [ ] Delete or archive this candidate folder
"""


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def _repo_root() -> Path:
    return next(p for p in Path(__file__).resolve().parents if (p / "pyproject.toml").exists())


def _candidates_root() -> Path:
    override = os.environ.get("TRACEFIX_PATTERN_REPOSITORY_DIR", "").strip()
    if override:
        return Path(override).expanduser().resolve()
    return _repo_root() / "tracefix" / "protocol_candidates"


def repository_enabled() -> bool:
    return os.environ.get("TRACEFIX_PATTERN_REPOSITORY_ENABLED", "true").strip().lower() not in (
        "false", "0", "no",
    )


def harvest_single_agent_enabled() -> bool:
    return os.environ.get("TRACEFIX_HARVEST_SINGLE_AGENT", "false").strip().lower() in (
        "true", "1", "yes",
    )


# ---------------------------------------------------------------------------
# Topology normalisation
# ---------------------------------------------------------------------------

@dataclass
class NormalizedTopology:
    agents: list[str]
    edges: list[dict[str, str]]
    resources: list[str]
    shape: str
    agent_count: int
    channel_count: int
    resource_count: int
    topology_hash: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "agents": self.agents,
            "edges": self.edges,
            "resources": self.resources,
            "shape": self.shape,
            "agent_count": self.agent_count,
            "channel_count": self.channel_count,
            "resource_count": self.resource_count,
            "topology_hash": self.topology_hash,
        }


def normalize_topology(ir: dict[str, Any]) -> NormalizedTopology:
    """Strip task-specific names; produce a structural fingerprint.

    Agents are renamed A, B, C, …  in stable order (sorted by original id).
    Channels become directed edges in the normalised agent namespace.
    Resources become R1, R2, … (sorted by original id).
    """
    raw_agents: list[dict] = [a for a in (ir.get("agents") or []) if isinstance(a, dict)]
    raw_channels: list[dict] = [c for c in (ir.get("channels") or []) if isinstance(c, dict)]
    raw_resources: list[dict] = [r for r in (ir.get("resources") or []) if isinstance(r, dict)]

    # Collect all agent IDs
    all_agent_ids = [
        str(a.get("id") or a.get("name") or "?").strip() for a in raw_agents
    ]

    # Compute degree by original ID so we can assign canonical label by role.
    # Agents with higher out-degree get lower labels (they "initiate" communication).
    # Tiebreak: lower in-degree first (pure senders before receivers/forwarders),
    # then alphabetically for full determinism.
    out_deg: dict[str, int] = {aid: 0 for aid in all_agent_ids}
    in_deg: dict[str, int] = {aid: 0 for aid in all_agent_ids}
    for ch in raw_channels:
        frm = str(ch.get("from") or "").strip()
        to = str(ch.get("to") or "").strip()
        if frm in out_deg:
            out_deg[frm] += 1
        if to in in_deg:
            in_deg[to] += 1

    # Sort key: (-out_degree, in_degree, id_lower) → senders first, receivers last.
    agent_ids = sorted(
        all_agent_ids,
        key=lambda aid: (-out_deg.get(aid, 0), in_deg.get(aid, 0), aid.lower()),
    )
    resource_ids = sorted(
        (str(r.get("id") or r.get("name") or "?").strip() for r in raw_resources),
        key=str.lower,
    )

    # Build rename maps
    _letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    agent_map: dict[str, str] = {
        orig: (_letters[i] if i < len(_letters) else f"A{i}")
        for i, orig in enumerate(agent_ids)
    }
    resource_map: dict[str, str] = {
        orig: f"R{i + 1}"
        for i, orig in enumerate(resource_ids)
    }

    # Normalised agents / resources
    norm_agents = [agent_map.get(aid, aid) for aid in agent_ids]
    norm_resources = [resource_map.get(rid, rid) for rid in resource_ids]

    # Normalised edges (channels)
    edges: list[dict[str, str]] = []
    for ch in raw_channels:
        frm = str(ch.get("from") or "").strip()
        to = str(ch.get("to") or "").strip()
        norm_from = agent_map.get(frm, frm)
        norm_to = agent_map.get(to, to)
        if norm_from and norm_to:
            edges.append({"from": norm_from, "to": norm_to})

    # Deduplicate edges by keeping unique (from, to) pairs in insertion order
    seen_edges: set[tuple[str, str]] = set()
    deduped_edges: list[dict[str, str]] = []
    for e in edges:
        key = (e["from"], e["to"])
        if key not in seen_edges:
            seen_edges.add(key)
            deduped_edges.append(e)

    shape = _classify_shape(norm_agents, deduped_edges)

    # Stable hash: sort edge list so hash is independent of channel declaration order
    hash_payload = json.dumps(
        {
            "agents": sorted(norm_agents),
            "edges": sorted(deduped_edges, key=lambda e: (e["from"], e["to"])),
            "resources": sorted(norm_resources),
            "shape": shape,
        },
        separators=(",", ":"),
        sort_keys=True,
    )
    topology_hash = hashlib.sha256(hash_payload.encode()).hexdigest()[:16]

    return NormalizedTopology(
        agents=norm_agents,
        edges=deduped_edges,
        resources=norm_resources,
        shape=shape,
        agent_count=len(norm_agents),
        channel_count=len(deduped_edges),
        resource_count=len(norm_resources),
        topology_hash=topology_hash,
    )


def _classify_shape(agents: list[str], edges: list[dict[str, str]]) -> str:
    n = len(agents)
    e = len(edges)
    if n == 0:
        return "empty"
    if n == 1:
        return "single"
    if e == 0:
        return "isolated"

    # Build adjacency
    out_degree: dict[str, int] = {a: 0 for a in agents}
    in_degree: dict[str, int] = {a: 0 for a in agents}
    bidirectional: set[tuple[str, str]] = set()
    fwd: set[tuple[str, str]] = set()
    for edge in edges:
        f, t = edge["from"], edge["to"]
        out_degree[f] = out_degree.get(f, 0) + 1
        in_degree[t] = in_degree.get(t, 0) + 1
        fwd.add((f, t))

    for (f, t) in fwd:
        if (t, f) in fwd:
            bidirectional.add((min(f, t), max(f, t)))

    bidi_count = len(bidirectional)

    if n == 2:
        if bidi_count == 1:
            return "bidirectional_pair"
        return "sequential_handoff"

    # Chain: each node has at most 1 in and 1 out, no back-edges
    if e == n - 1 and max(out_degree.values()) <= 1 and max(in_degree.values()) <= 1:
        return "chain"

    # Star: one hub connected to all others, no edges among spokes
    for a in agents:
        others = [x for x in agents if x != a]
        hub_out = all((a, o) in fwd or (o, a) in fwd for o in others)
        spoke_edges = sum(
            1 for edge in edges
            if edge["from"] != a and edge["to"] != a
        )
        if hub_out and spoke_edges == 0:
            return "star"

    # Ring: every node has exactly 1 in and 1 out
    if max(out_degree.values()) == 1 and max(in_degree.values()) == 1 and e == n:
        return "ring"

    # Fully connected (n*(n-1) directed edges or n*(n-1)/2 pairs)
    if e >= n * (n - 1):
        return "fully_connected"

    return "custom_graph"


# ---------------------------------------------------------------------------
# Suggested pattern name
# ---------------------------------------------------------------------------

def _suggest_pattern_name(topology: NormalizedTopology) -> str:
    n = topology.agent_count
    c = topology.channel_count
    s = topology.shape
    mapping = {
        "single": "single_agent",
        "sequential_handoff": "sequential_handoff",
        "bidirectional_pair": "bidirectional_pair",
        "chain": f"chain_{n}",
        "star": f"star_{n}",
        "ring": f"ring_{n}",
        "fully_connected": f"fully_connected_{n}",
        "isolated": f"isolated_{n}",
        "custom_graph": f"custom_{n}a_{c}c",
    }
    return mapping.get(s, f"pattern_{n}a_{c}c_{s}")


# ---------------------------------------------------------------------------
# Candidate ID generation
# ---------------------------------------------------------------------------

def _candidate_id(topology_hash: str, created_at: str) -> str:
    ts = created_at[:19].replace(":", "").replace("-", "").replace("T", "_")
    return f"cand_{topology_hash}_{ts}"


# ---------------------------------------------------------------------------
# Harvest entry point
# ---------------------------------------------------------------------------

@dataclass
class HarvestResult:
    attempted: bool = False
    saved: bool = False
    deduplicated: bool = False
    candidate_id: str = ""
    candidate_path: str = ""
    topology_hash: str = ""
    usage_count: int = 0
    skip_reason: str = ""
    error: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "attempted": self.attempted,
            "saved": self.saved,
            "deduplicated": self.deduplicated,
            "candidate_id": self.candidate_id,
            "candidate_path": self.candidate_path,
            "topology_hash": self.topology_hash,
            "usage_count": self.usage_count,
            "skip_reason": self.skip_reason,
            "error": self.error,
        }


def harvest_candidate(
    workspace: Path,
    *,
    task_text: str = "",
    used_opencode_fallback: bool = True,
    matched_existing_template: bool = False,
    is_single_agent: bool = False,
    is_custom_task: bool = True,
    timing_report_path: Path | None = None,
) -> HarvestResult:
    """Attempt to save a verified workspace as a pattern candidate.

    Call this ONLY after:
    - IR validation passed
    - PlusCal/TLC passed (summary.json#tlc_passed is True)
    - Protocol.tla exists

    Returns a HarvestResult describing what happened.
    """
    result = HarvestResult(attempted=True)

    try:
        # --- Guard: repository enabled? ---
        if not repository_enabled():
            result.skip_reason = "TRACEFIX_PATTERN_REPOSITORY_ENABLED=false"
            return result

        # --- Guard: single-agent harvest allowed? ---
        if is_single_agent and not harvest_single_agent_enabled():
            result.skip_reason = (
                "single-agent fast path; set TRACEFIX_HARVEST_SINGLE_AGENT=true to harvest"
            )
            return result

        # --- Guard: only harvest when OpenCode was used or no template matched ---
        if not used_opencode_fallback and matched_existing_template and not is_custom_task:
            result.skip_reason = "static template matched; no novel pattern to harvest"
            return result

        # --- Read workspace artifacts ---
        spec = workspace / "spec"
        ir_path = spec / "ir.json"
        tla_path = spec / "Protocol.tla"
        cfg_path = spec / "Protocol.cfg"
        summary_path = spec / "summary.json"

        if not ir_path.exists():
            result.skip_reason = "spec/ir.json missing; cannot harvest"
            return result
        if not tla_path.exists():
            result.skip_reason = "spec/Protocol.tla missing; cannot harvest"
            return result

        ir: dict[str, Any] = {}
        try:
            ir = json.loads(ir_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            result.error = f"failed to read ir.json: {exc}"
            return result

        # Verify TLC really passed (never trust the caller alone)
        tlc_passed = False
        if summary_path.exists():
            try:
                summary = json.loads(summary_path.read_text(encoding="utf-8"))
                tlc_passed = bool(summary.get("tlc_passed"))
            except (OSError, json.JSONDecodeError):
                pass
        if not tlc_passed:
            result.skip_reason = "tlc_passed is not True in spec/summary.json; not harvesting"
            return result

        # --- Normalise topology ---
        topology = normalize_topology(ir)
        result.topology_hash = topology.topology_hash

        candidates_root = _candidates_root()
        candidates_root.mkdir(parents=True, exist_ok=True)

        # --- Deduplication check ---
        existing = _find_by_hash(candidates_root, topology.topology_hash)
        if existing is not None:
            _update_existing(
                existing,
                source_workspace=str(workspace),
                task_text=task_text,
            )
            meta = _read_json(existing / "candidate_metadata.json")
            result.deduplicated = True
            result.saved = True
            result.candidate_id = str(meta.get("candidate_id", existing.name))
            result.candidate_path = str(existing)
            result.usage_count = int(meta.get("usage_count", 1))
            return result

        # --- Create new candidate ---
        created_at = datetime.now(timezone.utc).isoformat()
        cand_id = _candidate_id(topology.topology_hash, created_at)
        cand_dir = candidates_root / cand_id
        cand_dir.mkdir(parents=True, exist_ok=True)

        with _WRITE_LOCK:
            _write_candidate(
                cand_dir,
                candidate_id=cand_id,
                workspace=workspace,
                ir=ir,
                topology=topology,
                task_text=task_text,
                created_at=created_at,
                tlc_passed=tlc_passed,
                used_opencode_fallback=used_opencode_fallback,
                matched_existing_template=matched_existing_template,
                tla_path=tla_path,
                cfg_path=cfg_path,
                timing_report_path=timing_report_path,
            )

        result.saved = True
        result.candidate_id = cand_id
        result.candidate_path = str(cand_dir)
        result.usage_count = 1
        print(
            f"[PATTERN_REPO] saved candidate {cand_id} "
            f"hash={topology.topology_hash} shape={topology.shape} "
            f"agents={topology.agent_count} channels={topology.channel_count}",
            flush=True,
        )
        return result

    except Exception as exc:  # noqa: BLE001 - harvest must never kill the pipeline
        result.error = str(exc)
        print(f"[PATTERN_REPO] harvest error (non-fatal): {exc}", file=sys.stderr, flush=True)
        return result


# ---------------------------------------------------------------------------
# Candidate write helpers
# ---------------------------------------------------------------------------

def _write_candidate(
    cand_dir: Path,
    *,
    candidate_id: str,
    workspace: Path,
    ir: dict[str, Any],
    topology: NormalizedTopology,
    task_text: str,
    created_at: str,
    tlc_passed: bool,
    used_opencode_fallback: bool,
    matched_existing_template: bool,
    tla_path: Path,
    cfg_path: Path,
    timing_report_path: Path | None,
) -> None:
    suggested = _suggest_pattern_name(topology)

    metadata: dict[str, Any] = {
        "candidate_id": candidate_id,
        "source_workspace": str(workspace),
        "source_task_text": task_text[:500] if task_text else "",
        "created_at": created_at,
        "last_seen_at": created_at,
        "agents_count": topology.agent_count,
        "channels_count": topology.channel_count,
        "resources_count": topology.resource_count,
        "normalized_topology_hash": topology.topology_hash,
        "tlc_passed": tlc_passed,
        "pluscal_passed": True,
        "used_opencode_fallback": used_opencode_fallback,
        "matched_existing_template": matched_existing_template,
        "suggested_pattern_name": suggested,
        "promotion_status": "candidate",
        "usage_count": 1,
        "observed_workspaces": [str(workspace)],
        "observed_tasks": [task_text[:300]] if task_text else [],
        "notes": "",
    }

    _write_json(cand_dir / "candidate_metadata.json", metadata)
    _write_json(cand_dir / "normalized_topology.json", topology.as_dict())
    _write_json(cand_dir / "source_ir.json", ir)
    shutil.copy2(tla_path, cand_dir / "Protocol.tla")
    if cfg_path.exists():
        shutil.copy2(cfg_path, cand_dir / "Protocol.cfg")
    if timing_report_path and timing_report_path.exists():
        shutil.copy2(timing_report_path, cand_dir / "pipeline_timing_report.json")
    (cand_dir / "README.md").write_text(_README_BODY, encoding="utf-8")


def _find_by_hash(candidates_root: Path, topology_hash: str) -> Path | None:
    """Return the candidate directory whose metadata matches the hash, or None."""
    for cand_dir in candidates_root.iterdir():
        if not cand_dir.is_dir():
            continue
        meta_path = cand_dir / "candidate_metadata.json"
        if not meta_path.exists():
            continue
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            if meta.get("normalized_topology_hash") == topology_hash:
                return cand_dir
        except (OSError, json.JSONDecodeError):
            continue
    return None


def _update_existing(
    cand_dir: Path,
    *,
    source_workspace: str,
    task_text: str,
) -> None:
    meta_path = cand_dir / "candidate_metadata.json"
    with _WRITE_LOCK:
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            meta = {}
        meta["usage_count"] = int(meta.get("usage_count", 0)) + 1
        meta["last_seen_at"] = datetime.now(timezone.utc).isoformat()
        observed_ws: list[str] = meta.get("observed_workspaces") or []
        if source_workspace not in observed_ws:
            observed_ws.append(source_workspace)
        meta["observed_workspaces"] = observed_ws
        observed_tasks: list[str] = meta.get("observed_tasks") or []
        short_task = task_text[:300] if task_text else ""
        if short_task and short_task not in observed_tasks:
            observed_tasks.append(short_task)
        meta["observed_tasks"] = observed_tasks
        _write_json(meta_path, meta)
    print(
        f"[PATTERN_REPO] deduplicated candidate {meta.get('candidate_id')} "
        f"usage_count={meta.get('usage_count')}",
        flush=True,
    )


# ---------------------------------------------------------------------------
# IO helpers
# ---------------------------------------------------------------------------

def _write_json(path: Path, data: Any) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


# ---------------------------------------------------------------------------
# List candidates (CLI utility)
# ---------------------------------------------------------------------------

def list_candidates(
    candidates_root: Path | None = None,
) -> list[dict[str, Any]]:
    """Return a list of all candidate metadata dicts, sorted by created_at desc."""
    root = candidates_root or _candidates_root()
    results: list[dict[str, Any]] = []
    if not root.exists():
        return results
    for cand_dir in root.iterdir():
        if not cand_dir.is_dir():
            continue
        meta = _read_json(cand_dir / "candidate_metadata.json")
        if meta:
            results.append(meta)
    results.sort(key=lambda m: m.get("created_at") or "", reverse=True)
    return results


def _cli_list(candidates_root: Path | None = None) -> None:
    candidates = list_candidates(candidates_root)
    if not candidates:
        print("No pattern candidates found.")
        return
    header = (
        f"{'candidate_id':<46} {'pattern_name':<24} "
        f"{'A':>3} {'C':>3} {'R':>3} {'uses':>5} {'status':<12} last_seen"
    )
    print(header)
    print("-" * len(header))
    for m in candidates:
        print(
            f"{m.get('candidate_id', ''):<46} "
            f"{m.get('suggested_pattern_name', ''):<24} "
            f"{m.get('agents_count', 0):>3} "
            f"{m.get('channels_count', 0):>3} "
            f"{m.get('resources_count', 0):>3} "
            f"{m.get('usage_count', 0):>5} "
            f"{m.get('promotion_status', ''):<12} "
            f"{(m.get('last_seen_at') or '')[:19]}"
        )


def _cli_show(candidate_id: str, candidates_root: Path | None = None) -> None:
    root = candidates_root or _candidates_root()
    cand_dir = root / candidate_id
    if not cand_dir.is_dir():
        print(f"Candidate not found: {candidate_id}")
        sys.exit(1)
    meta = _read_json(cand_dir / "candidate_metadata.json")
    topo = _read_json(cand_dir / "normalized_topology.json")
    print(json.dumps({"metadata": meta, "topology": topo}, indent=2))


# ---------------------------------------------------------------------------
# __main__ — CLI entry point
# ---------------------------------------------------------------------------

def _main(argv: list[str] | None = None) -> None:
    args = (argv or sys.argv)[1:]
    sub = args[0] if args else "list"
    rest = args[1:]
    if sub == "list":
        _cli_list()
    elif sub == "show":
        if not rest:
            print("Usage: pattern_repository show <candidate_id>")
            sys.exit(1)
        _cli_show(rest[0])
    else:
        print(f"Unknown subcommand {sub!r}. Available: list, show")
        sys.exit(1)


if __name__ == "__main__":
    _main()
