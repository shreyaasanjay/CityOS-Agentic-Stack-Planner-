"""Protocol template: verifier_approver.

Two agents. Agent W (worker) performs domain work and submits it to Agent V
(verifier) for review. V checks the work and sends back either an approval or
a rejection. W receives the verdict and terminates.

Both agents own an independent lock resource. Two channels: one W→V (submit),
one V→W (verdict).

Coordination structure:
  Worker:   acquire work_lock → work → release work_lock →
            send "submit" → receive verdict → done
  Verifier: receive "submit" → acquire verify_lock → check →
            send "approved" OR "rejected" → release verify_lock → done

Deadlock proof sketch:
  Worker releases work_lock before sending, so Verifier can never be blocked
  waiting on work_lock. Verifier acquires only verify_lock (independent).
  Worker blocks on verdict channel AFTER releasing its lock and AFTER Verifier
  unblocks from the submit channel — no circular wait.

TLC safety properties verified:
  TypeInvariant   — locks and channels have correct types throughout
  NoOrphanLocks   — both locks FREE when both processes terminate
  ChannelsDrained — both channels empty when both processes terminate
  ChannelBound    — neither channel exceeds the depth bound
"""
from __future__ import annotations

from tracefix.pipeline.pipeline.pluscal_generator import _agent_id_to_const, _sanitize_id

PATTERN_ID = "verifier_approver"
DESCRIPTION = (
    "Worker performs work and submits to Verifier. Verifier approves or rejects. "
    "Worker receives the verdict. Independent locks. Two bidirectional channels."
)

_POSITIVE_KEYWORDS = frozenset({
    "verif", "check", "approv", "review", "audit", "validat",
    "inspect", "assess", "confirm", "evaluat",
})

_SEQUENTIAL_ONLY_KEYWORDS = frozenset({
    "then", "followed by", "next", "subsequently", "feeds into",
})

_NEGATIVE_KEYWORDS = frozenset({
    "aggregat", "collect", "gather", "parallel", "simultaneous",
    "multiple workers", "several agents",
})


def classify(task_lower: str, agent_count_hint: int, keywords: frozenset[str]) -> float:
    """Return a confidence score [0, 1] that this task matches verifier_approver."""
    if agent_count_hint != 2:
        return 0.0
    if any(kw in task_lower for kw in _NEGATIVE_KEYWORDS):
        return 0.0
    positive = sum(1 for kw in _POSITIVE_KEYWORDS if kw in task_lower)
    seq_only = sum(1 for kw in _SEQUENTIAL_ONLY_KEYWORDS if kw in task_lower)
    if positive == 0:
        return 0.0
    # Strong match: explicit verification keyword without plain handoff cues
    if positive >= 1 and seq_only == 0:
        return 0.88
    # Both sequential flow AND verification cue: lean toward verifier_approver
    if positive >= 1 and seq_only >= 1:
        return 0.80
    return 0.0


def build_template(params: dict) -> tuple[dict, str]:
    """Return (ir_data, protocol_tla) for the verifier_approver pattern.

    Expected params:
        worker_id       — ID for the worker agent (lowercase_snake_case)
        verifier_id     — ID for the verifier agent (lowercase_snake_case)
        worker_role     — human-readable role for the skip comment
        verifier_role   — human-readable role for the skip comment
        submit_label    — label for the work_to_verify channel (default "submit")
        approval_label  — label for the approved verdict (default "approved")
        rejection_label — label for the rejected verdict (default "rejected")
        channel_bound   — TLC channel depth bound (default 3)
    """
    w_id: str = params["worker_id"]
    v_id: str = params["verifier_id"]
    w_role: str = params.get("worker_role", f"perform work as {w_id}")
    v_role: str = params.get("verifier_role", f"verify work from {w_id}")
    submit_lbl: str = params.get("submit_label", "submit")
    approved_lbl: str = params.get("approval_label", "approved")
    rejected_lbl: str = params.get("rejection_label", "rejected")
    channel_bound: int = int(params.get("channel_bound", 3))

    w_var = _sanitize_id(w_id)
    v_var = _sanitize_id(v_id)
    w_const = _agent_id_to_const(w_id)
    v_const = _agent_id_to_const(v_id)

    ch_wv = f"{w_var}_to_{v_var}"   # worker → verifier
    ch_vw = f"{v_var}_to_{w_var}"   # verifier → worker
    lock_w_var = f"{w_var}_resource"
    lock_v_var = f"{v_var}_resource"
    lock_w_id = f"{w_id}_resource"
    lock_v_id = f"{v_id}_resource"
    ch_wv_id = f"{w_id}_to_{v_id}"
    ch_vw_id = f"{v_id}_to_{w_id}"

    ir_data: dict = {
        "agents": [
            {"id": w_id, "role": w_role},
            {"id": v_id, "role": v_role},
        ],
        "resources": [
            {"id": lock_w_id, "type": "Lock"},
            {"id": lock_v_id, "type": "Lock"},
        ],
        "channels": [
            {
                "id": ch_wv_id,
                "from": w_id,
                "to": v_id,
                "labels": [submit_lbl],
            },
            {
                "id": ch_vw_id,
                "from": v_id,
                "to": w_id,
                "labels": [approved_lbl, rejected_lbl],
            },
        ],
    }

    const_set = f"{w_const}, {v_const}"
    all_consts = "{" + w_const + ", " + v_const + "}"
    lock_type_set = "{" + w_const + ', ' + v_const + ', "FREE"}'

    lines = [
        "---- MODULE Protocol ----",
        "EXTENDS Integers, Sequences, TLC",
        "",
        f"CONSTANTS {const_set}",
        "",
        "(* --algorithm Protocol {",
        "variables",
        f'  {ch_wv} = <<>>; \\* {w_id} -> {v_id}, labels: [\'{submit_lbl}\']',
        f'  {ch_vw} = <<>>; \\* {v_id} -> {w_id}, labels: [\'{approved_lbl}\', \'{rejected_lbl}\']',
        f'  {lock_w_var} = "FREE"; \\* Lock',
        f'  {lock_v_var} = "FREE"; \\* Lock',
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
        # Worker process
        f"fair process ({w_var}_proc \\in {{{w_const}}})",
        'variables msg = "";',
        "{",
        f"  {w_var}_start:",
        f"    acquire_lock({lock_w_var});",
        f"  {w_var}_work:",
        f"    skip; \\* domain: {w_role}",
        f"  {w_var}_release:",
        f"    release_lock({lock_w_var});",
        f"  {w_var}_send:",
        f'    send({ch_wv}, "{submit_lbl}");',
        f"  {w_var}_await:",
        f"    receive({ch_vw}, msg);",
        f"  {w_var}_done:",
        "    skip;",
        "}",
        "",
        # Verifier process
        f"fair process ({v_var}_proc \\in {{{v_const}}})",
        'variables msg = "";',
        "{",
        f"  {v_var}_start:",
        f"    receive({ch_wv}, msg);",
        f"  {v_var}_acquire:",
        f"    acquire_lock({lock_v_var});",
        f"  {v_var}_check:",
        f"    skip; \\* domain: {v_role}",
        f"  {v_var}_decide:",
        "    either {",
        f'      send({ch_vw}, "{approved_lbl}");',
        f"      goto {v_var}_release;",
        "    } or {",
        f'      send({ch_vw}, "{rejected_lbl}");',
        f"      goto {v_var}_release;",
        "    };",
        f"  {v_var}_release:",
        f"    release_lock({lock_v_var});",
        f"  {v_var}_done:",
        "    skip;",
        "}",
        "",
        "} *)",
        "",
        f'AllDone == \\A p \\in {all_consts}: pc[p] = "Done"',
        "",
        "TypeInvariant ==",
        f"  /\\ \\A p \\in {all_consts}: pc[p] \\in STRING",
        f"  /\\ {lock_w_var} \\in {lock_type_set}",
        f"  /\\ {lock_v_var} \\in {lock_type_set}",
        f"  /\\ {ch_wv} \\in Seq(STRING)",
        f"  /\\ {ch_vw} \\in Seq(STRING)",
        "",
        "NoOrphanLocks ==",
        f'  AllDone => ({lock_w_var} = "FREE" /\\ {lock_v_var} = "FREE")',
        "",
        "ChannelsDrained ==",
        f"  AllDone => (Len({ch_wv}) = 0 /\\ Len({ch_vw}) = 0)",
        "",
        "ChannelBound ==",
        f"  Len({ch_wv}) <= {channel_bound} /\\ Len({ch_vw}) <= {channel_bound}",
        "",
        "====",
    ]

    return ir_data, "\n".join(lines)
