"""Protocol template: producer_consumer.

Two agents. Producer generates items and sends them over a channel. Consumer
receives and processes them, acquiring an output resource lock to write results.

Producer holds no persistent lock (stateless generation). Consumer owns one
lock representing its output resource.

Coordination structure:
  Producer: skip (generate item) → send item → done
  Consumer: receive item → acquire output_lock → process → release output_lock → done

Deadlock proof sketch:
  Producer never acquires a lock, so it cannot be blocked by Consumer.
  Consumer blocks only on the channel (awaiting Producer) and then on its own
  output_lock — no other agent contends for output_lock.

TLC safety properties verified:
  TypeInvariant   — lock and channel have correct types throughout
  NoOrphanLocks   — consumer's lock is FREE when both processes terminate
  ChannelsDrained — channel is empty when both processes terminate
  ChannelBound    — channel never exceeds the depth bound
"""
from __future__ import annotations

from tracefix.pipeline.pipeline.pluscal_generator import _agent_id_to_const, _sanitize_id

PATTERN_ID = "producer_consumer"
DESCRIPTION = (
    "Producer generates and sends items. Consumer receives and processes them "
    "while holding an output resource lock. No acknowledgement from Consumer."
)

_POSITIVE_KEYWORDS = frozenset({
    "produc", "generat", "creat", "emit",
    "consum", "process", "ingest", "handle",
    "queue", "stream", "pipeline", "batch",
})

_NEGATIVE_KEYWORDS = frozenset({
    "approves", "approve", "rejects", "reject", "verif",
    "check back", "returns result", "acknowledge",
    "confirm", "feedback", "respond", "reply",
    "multiple consumer", "multiple worker",
})

_SEQUENTIAL_CUES = frozenset({
    "then", "followed by", "next", "feeds into", "subsequently",
    "passes to", "delivers to", "forwards to",
})


def classify(task_lower: str, agent_count_hint: int, keywords: frozenset[str]) -> float:
    """Return a confidence score [0, 1] that this task matches producer_consumer."""
    if agent_count_hint != 2:
        return 0.0
    if any(kw in task_lower for kw in _NEGATIVE_KEYWORDS):
        return 0.0
    positive = sum(1 for kw in _POSITIVE_KEYWORDS if kw in task_lower)
    seq = sum(1 for kw in _SEQUENTIAL_CUES if kw in task_lower)
    if positive == 0:
        return 0.0
    # Explicit producer AND consumer keywords → strong match
    has_producer = any(kw in task_lower for kw in {"produc", "generat", "creat", "emit"})
    has_consumer = any(kw in task_lower for kw in {"consum", "process", "ingest", "handle"})
    if has_producer and has_consumer:
        return 0.92
    if positive >= 2:
        return 0.82
    if positive == 1 and seq >= 1:
        return 0.76
    return 0.0


def build_template(params: dict) -> tuple[dict, str]:
    """Return (ir_data, protocol_tla) for the producer_consumer pattern.

    Expected params:
        producer_id    — ID for the producer agent (lowercase_snake_case)
        consumer_id    — ID for the consumer agent (lowercase_snake_case)
        producer_role  — human-readable role for the skip comment
        consumer_role  — human-readable role for the skip comment
        item_label     — channel label string (default "item")
        channel_bound  — TLC channel depth bound (default 3)
    """
    p_id: str = params["producer_id"]
    c_id: str = params["consumer_id"]
    p_role: str = params.get("producer_role", f"generate item as {p_id}")
    c_role: str = params.get("consumer_role", f"process item from {p_id}")
    item_lbl: str = params.get("item_label", "item")
    channel_bound: int = int(params.get("channel_bound", 3))

    p_var = _sanitize_id(p_id)
    c_var = _sanitize_id(c_id)
    p_const = _agent_id_to_const(p_id)
    c_const = _agent_id_to_const(c_id)

    ch_var = f"{p_var}_to_{c_var}"
    lock_c_var = f"{c_var}_output"
    lock_c_id = f"{c_id}_output"
    ch_id = f"{p_id}_to_{c_id}"

    ir_data: dict = {
        "agents": [
            {"id": p_id, "role": p_role},
            {"id": c_id, "role": c_role},
        ],
        "resources": [
            {"id": lock_c_id, "type": "Lock"},
        ],
        "channels": [
            {
                "id": ch_id,
                "from": p_id,
                "to": c_id,
                "labels": [item_lbl],
            }
        ],
    }

    all_consts = "{" + p_const + ", " + c_const + "}"
    lock_type_set = "{" + p_const + ', ' + c_const + ', "FREE"}'

    lines = [
        "---- MODULE Protocol ----",
        "EXTENDS Integers, Sequences, TLC",
        "",
        f"CONSTANTS {p_const}, {c_const}",
        "",
        "(* --algorithm Protocol {",
        "variables",
        f'  {ch_var} = <<>>; \\* {p_id} -> {c_id}, labels: [\'{item_lbl}\']',
        f'  {lock_c_var} = "FREE"; \\* Lock',
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
        # Producer process — no lock
        f"fair process ({p_var}_proc \\in {{{p_const}}})",
        'variables msg = "";',
        "{",
        f"  {p_var}_start:",
        f"    skip; \\* domain: {p_role}",
        f"  {p_var}_send:",
        f'    send({ch_var}, "{item_lbl}");',
        f"  {p_var}_done:",
        "    skip;",
        "}",
        "",
        # Consumer process — owns output lock
        f"fair process ({c_var}_proc \\in {{{c_const}}})",
        'variables msg = "";',
        "{",
        f"  {c_var}_start:",
        f"    receive({ch_var}, msg);",
        f"  {c_var}_acquire:",
        f"    acquire_lock({lock_c_var});",
        f"  {c_var}_process:",
        f"    skip; \\* domain: {c_role}",
        f"  {c_var}_release:",
        f"    release_lock({lock_c_var});",
        f"  {c_var}_done:",
        "    skip;",
        "}",
        "",
        "} *)",
        "",
        f'AllDone == \\A p \\in {all_consts}: pc[p] = "Done"',
        "",
        "TypeInvariant ==",
        f"  /\\ \\A p \\in {all_consts}: pc[p] \\in STRING",
        f"  /\\ {lock_c_var} \\in {lock_type_set}",
        f"  /\\ {ch_var} \\in Seq(STRING)",
        "",
        "NoOrphanLocks ==",
        f'  AllDone => ({lock_c_var} = "FREE")',
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
