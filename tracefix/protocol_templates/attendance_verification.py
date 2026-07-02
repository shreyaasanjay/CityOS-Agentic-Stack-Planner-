"""Protocol template: attendance_verification.

Two agents. Agent O (observer) collects occupancy sensor data and attendance
count, then sends evidence to Agent V (verifier). V compares observed occupancy
against expected attendance, then sends a verdict report back to O.

This pattern matches smart-room queries about whether occupancy matches expected
attendance, conflicting/insufficient evidence, or attendance mismatch detection.

Coordination structure:
  Observer: acquire obs_lock → collect evidence → release obs_lock →
            send "evidence" → receive report → done
  Verifier: receive "evidence" → acquire ver_lock → compare/assess →
            send "matched" OR "mismatch" OR "insufficient" →
            release ver_lock → done

Deadlock proof sketch:
  Observer releases obs_lock before sending, so Verifier can never be blocked
  on obs_lock. Verifier acquires only ver_lock (independent).
  Observer blocks on report channel AFTER releasing its lock — no circular wait.

TLC safety properties verified:
  TypeInvariant   — locks and channels have correct types throughout
  NoOrphanLocks   — both locks FREE when both processes terminate
  ChannelsDrained — both channels empty when both processes terminate
  ChannelBound    — neither channel exceeds the depth bound
"""
from __future__ import annotations

from tracefix.pipeline.pipeline.pluscal_generator import _agent_id_to_const, _sanitize_id

PATTERN_ID = "attendance_verification"
DESCRIPTION = (
    "Observer collects occupancy sensor data and attendance count, sends evidence "
    "to Verifier. Verifier compares observed vs expected and sends a verdict report "
    "(matched / mismatch / insufficient). Two independent locks. Two channels."
)

# Keywords that raise confidence this is an attendance-verification pattern.
# Presence of domain-specific terms (sensor, calendar, attendance, occupancy)
# combined with comparison/evidence terms distinguishes this from generic verifier_approver.
_POSITIVE_KEYWORDS = frozenset({
    "occupancy", "attendance", "match", "observed", "expected",
    "evidence", "conflicting", "insufficient", "discrepancy", "mismatch",
    "whether", "sensor", "calendar", "count", "report",
})

# At least one of these must be present for a high-confidence match.
_DOMAIN_KEYWORDS = frozenset({
    "occupancy", "attendance", "sensor", "calendar",
})


def classify(task_lower: str, agent_count_hint: int, keywords: frozenset[str]) -> float:
    """Return a confidence score [0, 1] that this task matches attendance_verification."""
    if agent_count_hint != 2:
        return 0.0
    domain = sum(1 for kw in _DOMAIN_KEYWORDS if kw in task_lower)
    if domain == 0:
        return 0.0
    positive = sum(1 for kw in _POSITIVE_KEYWORDS if kw in task_lower)
    if positive == 0:
        return 0.0
    # Strong match: at least two domain-specific terms plus comparison/evidence cues
    if domain >= 2 or positive >= 3:
        return 0.88
    # Moderate match: one domain term plus at least one comparison/evidence cue
    if domain >= 1 and positive >= 2:
        return 0.82
    return 0.0


def build_template(params: dict) -> tuple[dict, str]:
    """Return (ir_data, protocol_tla) for the attendance_verification pattern.

    Expected params:
        observer_id     — ID for the observer agent (lowercase_snake_case)
        verifier_id     — ID for the verifier agent (lowercase_snake_case)
        observer_role   — human-readable role for the skip comment
        verifier_role   — human-readable role for the skip comment
        evidence_label  — label for the evidence channel message (default "evidence")
        matched_label   — label for a match verdict (default "matched")
        mismatch_label  — label for a mismatch verdict (default "mismatch")
        insufficient_label — label for insufficient-evidence verdict (default "insufficient")
        channel_bound   — TLC channel depth bound (default 3)
    """
    o_id: str = params["observer_id"]
    v_id: str = params["verifier_id"]
    o_role: str = params.get("observer_role", f"collect occupancy evidence as {o_id}")
    v_role: str = params.get("verifier_role", f"verify attendance match as {v_id}")
    evidence_lbl: str = params.get("evidence_label", "evidence")
    matched_lbl: str = params.get("matched_label", "matched")
    mismatch_lbl: str = params.get("mismatch_label", "mismatch")
    insufficient_lbl: str = params.get("insufficient_label", "insufficient")
    channel_bound: int = int(params.get("channel_bound", 3))

    o_var = _sanitize_id(o_id)
    v_var = _sanitize_id(v_id)
    o_const = _agent_id_to_const(o_id)
    v_const = _agent_id_to_const(v_id)

    ch_ov = f"{o_var}_to_{v_var}"   # observer → verifier (evidence)
    ch_vo = f"{v_var}_to_{o_var}"   # verifier → observer (report)
    lock_o_var = f"{o_var}_resource"
    lock_v_var = f"{v_var}_resource"
    lock_o_id = f"{o_id}_resource"
    lock_v_id = f"{v_id}_resource"
    ch_ov_id = f"{o_id}_to_{v_id}"
    ch_vo_id = f"{v_id}_to_{o_id}"

    ir_data: dict = {
        "agents": [
            {"id": o_id, "role": o_role},
            {"id": v_id, "role": v_role},
        ],
        "resources": [
            {"id": lock_o_id, "type": "Lock"},
            {"id": lock_v_id, "type": "Lock"},
        ],
        "channels": [
            {
                "id": ch_ov_id,
                "from": o_id,
                "to": v_id,
                "labels": [evidence_lbl],
            },
            {
                "id": ch_vo_id,
                "from": v_id,
                "to": o_id,
                "labels": [matched_lbl, mismatch_lbl, insufficient_lbl],
            },
        ],
    }

    all_consts = "{" + o_const + ", " + v_const + "}"
    const_set = f"{o_const}, {v_const}"
    lock_type_set = "{" + o_const + ', ' + v_const + ', "FREE"}'

    lines = [
        "---- MODULE Protocol ----",
        "EXTENDS Integers, Sequences, TLC",
        "",
        f"CONSTANTS {const_set}",
        "",
        "(* --algorithm Protocol {",
        "variables",
        f'  {ch_ov} = <<>>; \\* {o_id} -> {v_id}, labels: [\'{evidence_lbl}\']',
        f'  {ch_vo} = <<>>; \\* {v_id} -> {o_id}, labels: [\'{matched_lbl}\', \'{mismatch_lbl}\', \'{insufficient_lbl}\']',
        f'  {lock_o_var} = "FREE"; \\* Lock',
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
        # Observer process
        f"fair process ({o_var}_proc \\in {{{o_const}}})",
        'variables msg = "";',
        "{",
        f"  {o_var}_start:",
        f"    acquire_lock({lock_o_var});",
        f"  {o_var}_collect:",
        f"    skip; \\* domain: {o_role}",
        f"  {o_var}_release:",
        f"    release_lock({lock_o_var});",
        f"  {o_var}_send:",
        f'    send({ch_ov}, "{evidence_lbl}");',
        f"  {o_var}_await:",
        f"    receive({ch_vo}, msg);",
        f"  {o_var}_done:",
        "    skip;",
        "}",
        "",
        # Verifier process
        f"fair process ({v_var}_proc \\in {{{v_const}}})",
        'variables msg = "";',
        "{",
        f"  {v_var}_start:",
        f"    receive({ch_ov}, msg);",
        f"  {v_var}_acquire:",
        f"    acquire_lock({lock_v_var});",
        f"  {v_var}_compare:",
        f"    skip; \\* domain: {v_role}",
        f"  {v_var}_decide:",
        "    either {",
        f'      send({ch_vo}, "{matched_lbl}");',
        f"      goto {v_var}_release;",
        "    } or {",
        f'      send({ch_vo}, "{mismatch_lbl}");',
        f"      goto {v_var}_release;",
        "    } or {",
        f'      send({ch_vo}, "{insufficient_lbl}");',
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
        f"  /\\ {lock_o_var} \\in {lock_type_set}",
        f"  /\\ {lock_v_var} \\in {lock_type_set}",
        f"  /\\ {ch_ov} \\in Seq(STRING)",
        f"  /\\ {ch_vo} \\in Seq(STRING)",
        "",
        "NoOrphanLocks ==",
        f'  AllDone => ({lock_o_var} = "FREE" /\\ {lock_v_var} = "FREE")',
        "",
        "ChannelsDrained ==",
        f"  AllDone => (Len({ch_ov}) = 0 /\\ Len({ch_vo}) = 0)",
        "",
        "ChannelBound ==",
        f"  Len({ch_ov}) <= {channel_bound} /\\ Len({ch_vo}) <= {channel_bound}",
        "",
        "====",
    ]

    return ir_data, "\n".join(lines)
