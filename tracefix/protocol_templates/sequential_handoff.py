"""Protocol template: sequential_handoff.

Two agents. Agent A (producer) performs work then sends a single handoff
message to Agent B (consumer). B receives it and performs downstream work.
Both agents own an independent lock resource.

Coordination structure:
  Agent A: acquire lock → domain work → send message → release lock → done
  Agent B: receive message → acquire lock → domain work → release lock → done

TLC safety properties verified:
  TypeInvariant   — locks and channel have correct types throughout
  NoOrphanLocks   — both locks are FREE when both processes terminate
  ChannelsDrained — channel is empty when both processes terminate
  ChannelBound    — channel never exceeds the depth bound
"""
from __future__ import annotations

from tracefix.pipeline.pipeline.pluscal_generator import _agent_id_to_const, _sanitize_id

PATTERN_ID = "sequential_handoff"
DESCRIPTION = (
    "Agent A performs work and hands a result to Agent B via one channel. "
    "Both agents hold independent locks. No acknowledgement from B back to A."
)

# Keywords that raise confidence this is a sequential-handoff pattern.
_POSITIVE_KEYWORDS = frozenset({
    "then", "followed by", "passes to", "hands off", "handoff",
    "next", "after", "subsequently", "delivers to", "sends to",
    "feeds into", "gives to", "forwards to", "provides to",
})

# Keywords that lower confidence (suggest a richer pattern is needed).
_NEGATIVE_KEYWORDS = frozenset({
    "approves", "approve", "rejects", "reject", "verif",  # partial match
    "review", "check back", "returns result", "acknowledge",
    "confirm", "feedback", "respond", "reply",
})


def classify(task_lower: str, agent_count_hint: int, keywords: frozenset[str]) -> float:
    """Return a confidence score [0, 1] that this task matches sequential_handoff."""
    if agent_count_hint != 2:
        return 0.0
    positive = sum(1 for kw in _POSITIVE_KEYWORDS if kw in task_lower)
    negative = sum(1 for kw in _NEGATIVE_KEYWORDS if kw in task_lower)
    if negative > 0:
        return 0.0
    if positive >= 2:
        return 0.90
    if positive == 1:
        return 0.78
    # No direct keyword match but clean 2-agent structure with no negative signals.
    return 0.0


def build_template(params: dict) -> tuple[dict, str]:
    """Return (ir_data, protocol_tla) for the sequential_handoff pattern.

    Expected params:
        agent_a_id     — ID for the producer agent (lowercase_snake_case)
        agent_b_id     — ID for the consumer agent (lowercase_snake_case)
        agent_a_role   — human-readable role for the skip comment
        agent_b_role   — human-readable role for the skip comment
        handoff_label  — channel label string (default "handoff")
        channel_bound  — TLC channel depth bound (default 3)
    """
    a_id: str = params["agent_a_id"]
    b_id: str = params["agent_b_id"]
    a_role: str = params.get("agent_a_role", f"perform work as {a_id}")
    b_role: str = params.get("agent_b_role", f"process handoff from {a_id}")
    label: str = params.get("handoff_label", "handoff")
    channel_bound: int = int(params.get("channel_bound", 3))

    a_var = _sanitize_id(a_id)
    b_var = _sanitize_id(b_id)
    a_const = _agent_id_to_const(a_id)
    b_const = _agent_id_to_const(b_id)

    ch_var = f"{a_var}_to_{b_var}"
    lock_a_var = f"{a_var}_resource"
    lock_b_var = f"{b_var}_resource"
    lock_a_id = f"{a_id}_resource"
    lock_b_id = f"{b_id}_resource"
    ch_id = f"{a_id}_to_{b_id}"

    ir_data: dict = {
        "agents": [
            {"id": a_id, "role": a_role},
            {"id": b_id, "role": b_role},
        ],
        "resources": [
            {"id": lock_a_id, "type": "Lock"},
            {"id": lock_b_id, "type": "Lock"},
        ],
        "channels": [
            {
                "id": ch_id,
                "from": a_id,
                "to": b_id,
                "labels": [label],
            }
        ],
    }

    const_set = f"{a_const}, {b_const}"
    all_consts = "{" + a_const + ", " + b_const + "}"
    lock_type_set = "{" + a_const + ', ' + b_const + ', "FREE"}'

    lines = [
        "---- MODULE Protocol ----",
        "EXTENDS Integers, Sequences, TLC",
        "",
        f"CONSTANTS {const_set}",
        "",
        "(* --algorithm Protocol {",
        "variables",
        f'  {ch_var} = <<>>; \\* {a_id} -> {b_id}, labels: [\'{label}\']',
        f'  {lock_a_var} = "FREE"; \\* Lock',
        f'  {lock_b_var} = "FREE"; \\* Lock',
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
        "macro acquire_lock(lock) {",
        '  await lock = "FREE";',
        "  lock := self;",
        "}",
        "",
        "macro release_lock(lock) {",
        '  lock := "FREE";',
        "}",
        "",
        f"fair process ({a_var}_proc \\in {{{a_const}}})",
        'variables msg = "";',
        "{",
        f"  {a_var}_start:",
        f"    acquire_lock({lock_a_var});",
        f"  {a_var}_work:",
        f"    skip; \\* domain: {a_role}",
        f"  {a_var}_send:",
        f'    send({ch_var}, "{label}");',
        f"  {a_var}_release:",
        f"    release_lock({lock_a_var});",
        f"  {a_var}_done:",
        "    skip;",
        "}",
        "",
        f"fair process ({b_var}_proc \\in {{{b_const}}})",
        'variables msg = "";',
        "{",
        f"  {b_var}_start:",
        f"    receive({ch_var}, msg);",
        f"  {b_var}_acquire:",
        f"    acquire_lock({lock_b_var});",
        f"  {b_var}_work:",
        f"    skip; \\* domain: {b_role}",
        f"  {b_var}_release:",
        f"    release_lock({lock_b_var});",
        f"  {b_var}_done:",
        "    skip;",
        "}",
        "",
        "} *)",
        "",
        f'AllDone == \\A p \\in {all_consts}: pc[p] = "Done"',
        "",
        "TypeInvariant ==",
        f"  /\\ \\A p \\in {all_consts}: pc[p] \\in STRING",
        f"  /\\ {lock_a_var} \\in {lock_type_set}",
        f"  /\\ {lock_b_var} \\in {lock_type_set}",
        f"  /\\ {ch_var} \\in Seq(STRING)",
        "",
        "NoOrphanLocks ==",
        f'  AllDone => ({lock_a_var} = "FREE" /\\ {lock_b_var} = "FREE")',
        "",
        "ChannelsDrained ==",
        f"  AllDone => (Len({ch_var}) = 0)",
        "",
        "ChannelBound ==",
        f"  Len({ch_var}) <= {channel_bound}",
        "",
        "====",
    ]

    return ir_data, "\n".join(lines)
