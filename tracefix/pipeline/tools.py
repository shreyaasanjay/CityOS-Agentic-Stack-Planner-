"""Tool definitions wrapping v3 modules for the agentic verification loop.

Each tool is a function (ws: Workspace, **kwargs) -> str that returns a human-readable result.
Tools read/write files in the workspace directory.

Pipeline: IR (agents/resources/channels) -> PlusCal scaffold -> LLM fills process bodies -> verify_spec
"""

from __future__ import annotations

import json
import os as _os
import re as _re
from typing import Callable

from tracefix.pipeline.workspace import Workspace


# ---------------------------------------------------------------------------
# Tool schema definitions (provider-agnostic canonical format)
# ---------------------------------------------------------------------------

TOOL_SCHEMAS: list[dict] = [
    # -- Reasoning tools --
    {
        "name": "think",
        "description": (
            "Use this tool to think through your approach before acting. "
            "Call it to plan IR structure, analyze error traces, or reason about "
            "repair strategies. The content is recorded but has no side effects. "
            "You SHOULD call think() before writing ir.json and before each repair."
        ),
        "parameters": {
            "type": "object",
            "required": ["thoughts"],
            "properties": {
                "thoughts": {
                    "type": "string",
                    "description": "Your reasoning, analysis, or plan.",
                },
            },
        },
    },
    # -- File tools --
    {
        "name": "write_file",
        "description": (
            "Write content to a file in the workspace. Use this to create or update "
            "any file: ir.json, Protocol.tla, notes/*.md, etc. When ir.json is written, "
            "downstream files (Protocol.tla, Protocol.cfg, tlc_output.log, tlc_error.md) "
            "are automatically cleared."
        ),
        "parameters": {
            "type": "object",
            "required": ["path", "content"],
            "properties": {
                "path": {
                    "type": "string",
                    "description": (
                        "Relative path within the workspace, e.g. 'ir.json', "
                        "'Protocol.tla', 'notes/analysis.md'."
                    ),
                },
                "content": {
                    "type": "string",
                    "description": "File content to write.",
                },
            },
        },
    },
    {
        "name": "read_file",
        "description": (
            "Read a file from the workspace. Returns the file content or an error "
            "if the file does not exist. Use to inspect ir.json, Protocol.tla, "
            "PLUSCAL_RULES.md, tlc_output.log, tlc_error.md, notes/*.md, etc."
        ),
        "parameters": {
            "type": "object",
            "required": ["path"],
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Relative path within the workspace.",
                },
            },
        },
    },
    {
        "name": "edit_file",
        "description": (
            "Make a precise edit to a file by replacing an exact string match. "
            "Provide the old_string to find and new_string to replace it with. "
            "The old_string must appear exactly once in the file (unless replace_all is true). "
            "Use this for surgical edits to Protocol.tla process bodies or ir.json. "
            "When editing ir.json, downstream files are auto-cleared. "
            "If the exact text doesn't match (e.g. due to whitespace or indentation "
            "differences), a whitespace-tolerant fallback will try to find a unique "
            "match by line — but for best results, call read_file first and copy "
            "old_string exactly as shown."
        ),
        "parameters": {
            "type": "object",
            "required": ["path", "old_string", "new_string"],
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Relative path within the workspace.",
                },
                "old_string": {
                    "type": "string",
                    "description": "The exact text to find and replace. Must match exactly (including whitespace).",
                },
                "new_string": {
                    "type": "string",
                    "description": "The replacement text.",
                },
                "replace_all": {
                    "type": "boolean",
                    "description": "Replace all occurrences instead of requiring uniqueness (default false).",
                    "default": False,
                },
            },
        },
    },
    {
        "name": "list_files",
        "description": "List all files in the workspace directory.",
        "parameters": {"type": "object", "properties": {}},
    },
    # -- Verification tools --
    {
        "name": "validate_ir",
        "description": (
            "Validate ir.json against the v3 JSON schema and semantic rules. "
            "Checks agents, resources, and channels (not states — those are PlusCal). "
            "Returns 'Valid' or a list of errors."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "compile_scaffold",
        "description": (
            "Generate the PlusCal scaffold from ir.json. Reads agents/resources/channels "
            "and writes Protocol.tla (with PlusCal boilerplate, macros, and process stubs) "
            "plus Protocol.cfg. You then fill in the process bodies with PlusCal code "
            "using edit_file, then call verify_spec to translate and model-check."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "verify_spec",
        "description": (
            "One-step verification: validate IR, read Protocol.tla, translate PlusCal "
            "(pcal.trans), and run TLC model checker. Returns PASS/FAIL with error details "
            "inline. If PlusCal has syntax errors, returns the pcal.trans error with line "
            "numbers. If TLC finds violations, returns the error trace. This is the "
            "preferred verification tool — use after filling in PlusCal process bodies."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "timeout": {
                    "type": "integer",
                    "description": "TLC timeout in seconds (default 120).",
                    "default": 120,
                },
            },
        },
    },
    {
        "name": "extract_states",
        "description": (
            "Extract per-agent state machine from the verified Protocol. "
            "Reads Protocol_translated.tla and ir.json, writes states.json. "
            "Call after verify_spec returns PASS. Required before Phase 4 prompt generation."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
    # -- Workflow tools --
    {
        "name": "load_benchmark",
        "description": (
            "Load a benchmark task and write task.md, tools.json, and metadata.json "
            "to the workspace. task.md is the task description; tools.json is the "
            "per-agent domain tool schemas (filter by agent_ids); metadata.json is "
            "the canonical source for agent/resource IDs — IR names MUST match it.\n"
            "Task IDs like '1E', '2M', '5H' "
            "(33 coordination tasks across 11 scenarios, 3 difficulties)."
        ),
        "parameters": {
            "type": "object",
            "required": ["task_id"],
            "properties": {
                "task_id": {
                    "type": "string",
                    "description": (
                        "Benchmark task ID: '1E', '2M', '5H', '10H', etc. "
                        "Format: {scenario}{difficulty} where scenario=1-11, difficulty=E/M/H."
                    ),
                },
            },
        },
    },
]


# ---------------------------------------------------------------------------
# File tools
# ---------------------------------------------------------------------------


def write_file(ws: Workspace, *, path: str, content: str) -> str:
    """Write content to a file in the workspace."""
    try:
        ws.write_file(path, content)
    except ValueError as e:
        return f"ERROR: {e}"

    # Provide summary for ir.json
    if path == "ir.json":
        ir_data = ws.read_ir()
        if ir_data is None:
            return (
                "Wrote ir.json but it is not valid JSON. "
                "Fix the content and write again."
            )
        agents = ir_data.get("agents", [])
        resources = ir_data.get("resources", [])
        channels = ir_data.get("channels", [])
        return (
            f"Wrote ir.json. Downstream files cleared.\n"
            f"  Agents: {len(agents)} ({', '.join(a.get('id', '?') for a in agents)})\n"
            f"  Resources: {len(resources)}"
            + (
                f" ({', '.join(r.get('id', '?') + ' (' + r.get('type', '?') + ')' for r in resources)})"
                if resources
                else ""
            )
            + f"\n  Channels: {len(channels)}"
            + (
                f" ({', '.join(c.get('id', '?') for c in channels)})"
                if channels
                else ""
            )
        )

    return f"Wrote {path} ({len(content)} bytes)."


def _normalize_line_for_match(line: str) -> str:
    """Collapse whitespace within a line and strip leading/trailing whitespace.

    Used for fuzzy matching when an exact old_string match fails — models
    frequently reproduce the right text with slightly different indentation
    or run-together spaces.
    """
    return _re.sub(r"\s+", " ", line.strip())


def _find_fuzzy_line_match(content: str, old_string: str) -> tuple[int, int] | None:
    """Find a unique whitespace-normalized match for old_string in content.

    Returns (start_offset, end_offset) into the ORIGINAL content string for
    the contiguous run of lines whose normalized form matches old_string's
    normalized lines, or None if there is no match or more than one match.
    Blank lines in old_string are ignored when building the pattern, since
    these are the most common source of spurious mismatches.
    """
    old_lines = [_normalize_line_for_match(l) for l in old_string.splitlines()]
    old_lines = [l for l in old_lines if l]
    if not old_lines:
        return None

    content_lines = content.splitlines(keepends=True)
    norm_content_lines = [_normalize_line_for_match(l) for l in content_lines]

    matches: list[tuple[int, int]] = []
    n = len(content_lines)
    m = len(old_lines)
    for start in range(n - m + 1):
        # Skip blank lines when aligning, but only allow matching against
        # non-blank normalized content lines for a tight comparison.
        window = [norm_content_lines[start + i] for i in range(m)]
        window = [w for w in window if w]
        if window == old_lines:
            # Compute original character offsets for this line range.
            start_offset = sum(len(l) for l in content_lines[:start])
            end_offset = sum(len(l) for l in content_lines[: start + m])
            matches.append((start_offset, end_offset))

    if len(matches) == 1:
        return matches[0]
    return None


def edit_file(
    ws: Workspace, *, path: str, old_string: str, new_string: str, replace_all: bool = False
) -> str:
    """Replace an exact string match in a workspace file."""
    try:
        content = ws.read_file(path)
    except ValueError as e:
        return f"ERROR: {e}"

    if content is None:
        return f"ERROR: File not found: {path}"

    if old_string == new_string:
        return "ERROR: old_string and new_string are identical."

    count = content.count(old_string)
    if count == 0:
        # Fallback: try a whitespace-tolerant match before giving up. This
        # handles the common case where the model reproduces the right text
        # with slightly different indentation, spacing, or escape sequences
        # (e.g. \\* vs \*).
        fuzzy = _find_fuzzy_line_match(content, old_string)
        if fuzzy is None:
            return (
                f"ERROR: old_string not found in {path}. "
                f"Make sure it matches exactly (including whitespace and newlines). "
                f"Tip: call read_file('{path}') again and copy the text exactly "
                f"as shown, or use fewer/shorter lines in old_string to avoid "
                f"whitespace mismatches."
            )
        start_offset, end_offset = fuzzy
        new_content = content[:start_offset] + new_string + content[end_offset:]
        try:
            ws.write_file(path, new_content)
        except ValueError as e:
            return f"ERROR: {e}"
        result = (
            f"Edited {path}: replaced 1 occurrence (matched via whitespace-"
            f"tolerant fallback — old_string did not match exactly, but a "
            f"unique normalized match was found and replaced)."
        )
        if path == "ir.json":
            ir_data = ws.read_ir()
            if ir_data is None:
                result += "\nWARNING: ir.json is no longer valid JSON after edit."
            else:
                agents = ir_data.get("agents", [])
                channels = ir_data.get("channels", [])
                result += f" Downstream files cleared. ({len(agents)} agents, {len(channels)} channels)"
        return result

    if count > 1 and not replace_all:
        return (
            f"ERROR: old_string appears {count} times in {path}. "
            f"Provide more context to make it unique, or set replace_all=true."
        )

    new_content = content.replace(old_string, new_string) if replace_all else content.replace(old_string, new_string, 1)

    try:
        ws.write_file(path, new_content)
    except ValueError as e:
        return f"ERROR: {e}"

    replacements = count if replace_all else 1
    result = f"Edited {path}: replaced {replacements} occurrence(s)."

    # If ir.json was edited, show summary
    if path == "ir.json":
        ir_data = ws.read_ir()
        if ir_data is None:
            result += "\nWARNING: ir.json is no longer valid JSON after edit."
        else:
            agents = ir_data.get("agents", [])
            channels = ir_data.get("channels", [])
            result += f" Downstream files cleared. ({len(agents)} agents, {len(channels)} channels)"

    return result


def read_file(ws: Workspace, *, path: str) -> str:
    """Read a file from the workspace."""
    try:
        content = ws.read_file(path)
    except ValueError as e:
        return f"ERROR: {e}"

    if content is None:
        return f"File not found: {path}"
    return content


def list_files(ws: Workspace) -> str:
    """List all files in the workspace."""
    files = ws.list_files()
    if not files:
        return "Workspace is empty."
    return "\n".join(files)


def think(ws: Workspace, *, thoughts: str) -> str:
    """No-op reasoning tool. Records thoughts in conversation history."""
    return "OK"


# ---------------------------------------------------------------------------
# Verification tools
# ---------------------------------------------------------------------------


def validate_ir(ws: Workspace) -> str:
    """Validate ir.json against schema + semantic rules."""
    from tracefix.pipeline.pipeline.validator import validate_ir as _validate_ir

    ir_data = ws.read_ir()
    if ir_data is None:
        return "ERROR: No valid ir.json in workspace. Write ir.json first."

    result = _validate_ir(ir_data)

    # Record structured result
    ws.result.ir_valid = result.valid
    ws.result.ir_errors = list(result.errors)
    # Update latest repair attempt if one exists
    if ws.result.repairs:
        ws.result.repairs[-1].ir_valid = result.valid

    if result.valid:
        return "Valid. IR passes schema and semantic validation."
    else:
        errors = "\n".join(f"  - {e}" for e in result.errors)
        return f"INVALID. {len(result.errors)} error(s):\n{errors}"


def compile_scaffold(ws: Workspace) -> str:
    """Generate PlusCal scaffold from ir.json."""
    from tracefix.pipeline.pipeline.pluscal_generator import (
        generate_pluscal_scaffold,
        generate_tlc_config,
    )

    ir_data = ws.read_ir()
    if ir_data is None:
        return "ERROR: No valid ir.json in workspace. Write ir.json first."

    try:
        tla_scaffold = generate_pluscal_scaffold(ir_data)
        tlc_config = generate_tlc_config(ir_data)
    except Exception as e:
        return f"ERROR: Scaffold generation failed \u2014 {type(e).__name__}: {e}"

    ws.write_file("Protocol.tla", tla_scaffold)
    ws.write_file("Protocol.cfg", tlc_config)

    lines = tla_scaffold.split("\n")
    return (
        f"OK. Wrote Protocol.tla ({len(lines)} lines) + Protocol.cfg.\n"
        f"The process bodies contain 'skip' placeholders.\n"
        f"Before editing Protocol.tla, read PLUSCAL_RULES.md and follow it. "
        f"Use edit_file to replace each process body with PlusCal code, "
        f"then call verify_spec to translate and model-check."
    )


_LABEL_START_RE = _re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*:")
_LABEL_ONLY_RE = _re.compile(
    r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*:\s*(?:(?:\\\*.*)|(?:\(\*.*\)))?\s*$"
)


def _is_pluscal_comment_or_blank(line: str) -> bool:
    stripped = line.strip()
    return (
        not stripped
        or stripped.startswith("\\*")
        or (stripped.startswith("(*") and stripped.endswith("*)"))
    )


def _format_source_context(lines: list[str], line_num: int, radius: int = 3) -> str:
    start = max(1, line_num - radius)
    end = min(len(lines), line_num + radius)
    context = []
    for idx in range(start, end + 1):
        marker = ">>>" if idx == line_num else "   "
        context.append(f"{marker} {idx:4d} | {lines[idx - 1]}")
    return "\n".join(context)


def _lint_pluscal_label_structure(tla_content: str) -> str | None:
    """Catch empty/stacked labels before pcal.trans emits a cryptic error."""
    lines = tla_content.splitlines()
    pending_label: tuple[int, str] | None = None

    for line_num, line in enumerate(lines, start=1):
        if _is_pluscal_comment_or_blank(line):
            continue

        label_start = _LABEL_START_RE.match(line)
        if pending_label is not None:
            prev_line, prev_label = pending_label
            if label_start:
                current_label = label_start.group(1)
                return (
                    "Consecutive PlusCal labels detected. "
                    f"Label `{prev_label}` on line {prev_line} has no executable "
                    f"statement before label `{current_label}` on line {line_num}.\n"
                    "Fix: give every label a statement, or make the first label a "
                    "`while (TRUE)` loop label. Read PLUSCAL_RULES.md before editing.\n\n"
                    "Source context:\n"
                    + _format_source_context(lines, line_num)
                )
            if line.strip().startswith("}"):
                return (
                    "Empty PlusCal label detected. "
                    f"Label `{prev_label}` on line {prev_line} reaches a closing "
                    "brace before any executable statement.\n"
                    "Fix: add a statement such as `skip;`, or remove the empty label. "
                    "Read PLUSCAL_RULES.md before editing.\n\n"
                    "Source context:\n"
                    + _format_source_context(lines, line_num)
                )
            pending_label = None

        label_only = _LABEL_ONLY_RE.match(line)
        if label_only:
            pending_label = (line_num, label_only.group(1))

    if pending_label is not None:
        prev_line, prev_label = pending_label
        return (
            "Empty PlusCal label detected. "
            f"Label `{prev_label}` on line {prev_line} has no executable statement "
            "after it.\n"
            "Fix: add a statement such as `skip;`, or remove the empty label. "
            "Read PLUSCAL_RULES.md before editing.\n\n"
            "Source context:\n"
            + _format_source_context(lines, prev_line)
        )

    return None


# ---------------------------------------------------------------------------
# Batch lint: collect ALL detectable syntax issues in one pass, instead of
# returning on the first match. Enabled via TRACEFIX_BATCH_LINT=1 (see
# verify_spec). Each _scan_* helper returns a list of (line_num, message)
# tuples; _lint_pluscal_all merges, sorts, and formats them together so the
# LLM can address multiple issues per repair turn.
# ---------------------------------------------------------------------------

_PROCESS_RE = _re.compile(
    r"^\s*(?:fair(?:\+)?\s+)?process\s+\(?\s*([A-Za-z_][A-Za-z0-9_]*)"
)
_GOTO_RE = _re.compile(r"\bgoto\s+[A-Za-z_][A-Za-z0-9_]*\s*$")


def _scan_label_structure(lines: list[str]) -> list[tuple[int, str]]:
    """All stacked/empty label issues (batch version of the fast-fail check)."""
    issues: list[tuple[int, str]] = []
    pending_label: tuple[int, str] | None = None

    for line_num, line in enumerate(lines, start=1):
        if _is_pluscal_comment_or_blank(line):
            continue

        label_start = _LABEL_START_RE.match(line)
        if pending_label is not None:
            prev_line, prev_label = pending_label
            if label_start:
                current_label = label_start.group(1)
                issues.append((
                    prev_line,
                    f"Consecutive PlusCal labels: label `{prev_label}` (line {prev_line}) "
                    f"has no executable statement before label `{current_label}` "
                    f"(line {line_num}). Add a statement, or merge into a "
                    "`while (TRUE)` loop label.",
                ))
            elif line.strip().startswith("}"):
                issues.append((
                    prev_line,
                    f"Empty PlusCal label: label `{prev_label}` (line {prev_line}) "
                    "reaches a closing brace before any executable statement. "
                    "Add `skip;` or remove the label.",
                ))
            pending_label = None

        label_only = _LABEL_ONLY_RE.match(line)
        if label_only:
            pending_label = (line_num, label_only.group(1))

    if pending_label is not None:
        prev_line, prev_label = pending_label
        issues.append((
            prev_line,
            f"Empty PlusCal label: label `{prev_label}` (line {prev_line}) has no "
            "executable statement after it. Add `skip;` or remove the label.",
        ))

    return issues


def _scan_skip_with_true_antipattern(lines: list[str]) -> list[tuple[int, str]]:
    """Flag `skip;` immediately followed by `with ... := TRUE;` (common bad repair)."""
    issues: list[tuple[int, str]] = []
    for line_num in range(len(lines) - 1):
        cur = lines[line_num].strip()
        nxt = lines[line_num + 1].strip()
        if cur != "skip;":
            continue
        if _re.match(r"^with\b.*:=\s*TRUE\s*;?\s*$", nxt):
            issues.append((
                line_num + 2,
                f"Anti-pattern: `skip;` (line {line_num + 1}) immediately followed by "
                f"`{nxt}` (line {line_num + 2}). Do not replace `skip;` with a "
                "`with ... := TRUE;` assignment. Remove the `with` line, or replace "
                "`skip;` itself with the correct assignment statement.",
            ))
    return issues


def _scan_brace_balance(lines: list[str]) -> list[tuple[int, str]]:
    """Flag PlusCal processes whose `{`/`}` counts don't balance."""
    issues: list[tuple[int, str]] = []
    current: tuple[int, str] | None = None  # (start_line, process_name)
    depth = 0

    def _close(end_line: int) -> None:
        nonlocal current, depth
        if current is not None and depth != 0:
            start_line, name = current
            issues.append((
                start_line,
                f"Unbalanced braces in process `{name}` (starting line {start_line}): "
                f"net brace count is {depth:+d} by line {end_line}. "
                "Check for a missing or extra `{`/`}` in this process body.",
            ))
        current = None
        depth = 0

    for line_num, line in enumerate(lines, start=1):
        if _is_pluscal_comment_or_blank(line):
            continue
        proc_match = _PROCESS_RE.match(line)
        if proc_match:
            _close(line_num - 1)
            current = (line_num, proc_match.group(1))
        if current is not None:
            depth += line.count("{") - line.count("}")

    _close(len(lines))
    return issues


def _scan_goto_missing_semicolon(lines: list[str]) -> list[tuple[int, str]]:
    """Flag `goto Label` statements that don't end with `;`."""
    issues: list[tuple[int, str]] = []
    for line_num, line in enumerate(lines, start=1):
        if _is_pluscal_comment_or_blank(line):
            continue
        stripped = line.strip()
        if _re.match(r"^goto\s+[A-Za-z_][A-Za-z0-9_]*$", stripped):
            issues.append((
                line_num,
                f"Missing semicolon: `{stripped}` (line {line_num}) should end with "
                "`;` — PlusCal statements must be terminated.",
            ))
    return issues


def _lint_pluscal_all(tla_content: str) -> str | None:
    """Run all batch lint checks and return a single combined report.

    Unlike `_lint_pluscal_label_structure`, this scans the entire file and
    collects every issue found across all checks, so the repair agent can
    fix multiple problems in one repair turn instead of one `verify_spec`
    round trip per issue. Returns None if no issues were found.
    """
    lines = tla_content.splitlines()

    all_issues: list[tuple[int, str]] = []
    all_issues += _scan_label_structure(lines)
    all_issues += _scan_skip_with_true_antipattern(lines)
    all_issues += _scan_brace_balance(lines)
    all_issues += _scan_goto_missing_semicolon(lines)

    if not all_issues:
        return None

    all_issues.sort(key=lambda item: item[0])

    parts = [
        f"Found {len(all_issues)} PlusCal syntax issue(s). Fix ALL of them before "
        "calling verify_spec again — each can be addressed with its own edit_file "
        "call in this turn. Read PLUSCAL_RULES.md before editing.\n",
    ]
    for i, (line_num, message) in enumerate(all_issues, start=1):
        parts.append(f"### Issue {i} (line {line_num})\n")
        parts.append(message)
        parts.append("\nSource context:\n" + _format_source_context(lines, line_num))
        parts.append("")

    return "\n".join(parts)


def verify_spec(ws: Workspace, *, timeout: int = 120) -> str:
    """One-step verification: validate IR, translate PlusCal, run TLC."""

    from tracefix.pipeline.pipeline.validator import validate_ir as _validate_ir
    from tracefix.pipeline.pipeline.pluscal_compiler import translate_pluscal
    from tracefix.pipeline.pipeline.tlc_runner import run_tlc as _run_tlc
    from tracefix.pipeline.pipeline.error_formatter import format_tlc_error
    from tracefix.pipeline.pipeline.trace_parser import parse_trace
    from tracefix.pipeline.workspace import RepairAttempt

    # Track repair attempts: if previous TLC failed, this is a repair
    if ws.result.tlc_status == "fail":
        # Circuit breaker: abort if same violation persisted across 3 repairs
        if len(ws.result.repairs) >= 3:
            recent = [r.new_violation_type for r in ws.result.repairs[-3:]]
            if recent[0] and recent[0] == recent[1] == recent[2]:
                return (
                    f"CIRCUIT BREAKER: Same violation type ({recent[0]}) "
                    f"persisted across 3 consecutive repair attempts. "
                    f"The underlying issue is likely beyond automated repair. "
                    f"Consider redesigning the protocol or simplifying the IR."
                )

        ws.repair_count += 1
        ws.result.repairs.append(RepairAttempt(
            attempt=ws.repair_count,
            success=True,  # updated below if validation fails
            violation_type=ws.result.tlc_violation_type,
        ))

    # Step 1: Read + validate IR (agents/resources/channels)
    ir_data = ws.read_ir()
    if ir_data is None:
        return "ERROR: No valid ir.json in workspace. Write ir.json first."

    val_result = _validate_ir(ir_data)
    ws.result.ir_valid = val_result.valid
    ws.result.ir_errors = list(val_result.errors)
    if ws.result.repairs:
        ws.result.repairs[-1].ir_valid = val_result.valid

    if not val_result.valid:
        if ws.result.repairs:
            ws.result.repairs[-1].success = False
        errors = "\n".join(f"  - {e}" for e in val_result.errors)
        return f"INVALID IR. {len(val_result.errors)} error(s):\n{errors}"

    # Step 2: Read Protocol.tla from workspace
    tla_content = ws.read_tla()
    if tla_content is None:
        return (
            "ERROR: No Protocol.tla in workspace. "
            "Call compile_scaffold first, then fill in PlusCal process bodies."
        )

    cfg_content = ws.read_cfg() or ""

    # Step 3: Catch the most common PlusCal label-shape mistakes locally.
    # TRACEFIX_BATCH_LINT=1 reports ALL detectable issues at once (fewer
    # verify_spec round trips); default reports only the first issue found.
    if _os.environ.get("TRACEFIX_BATCH_LINT") == "1":
        label_error = _lint_pluscal_all(tla_content)
        precheck_title = "PlusCal Syntax Precheck (batch)"
    else:
        label_error = _lint_pluscal_label_structure(tla_content)
        precheck_title = "PlusCal Syntax Precheck"
    if label_error:
        ws.result.tlc_status = "fail"
        ws.result.tlc_violation_type = "pcal_error"
        if ws.result.repairs:
            ws.result.repairs[-1].tlc_passed = False
            ws.result.repairs[-1].new_violation_type = "pcal_error"

        ws.write_file("tlc_error.md", f"# {precheck_title}\n\n{label_error}")
        ws._backup_protocol_attempt()
        return f"FAIL - {precheck_title}:\n{label_error}"

    # Step 4: Translate PlusCal -> TLA+
    pcal_result = translate_pluscal(tla_content, cfg_content)

    if not pcal_result.success:
        ws.result.tlc_status = "fail"
        ws.result.tlc_violation_type = "pcal_error"
        if ws.result.repairs:
            ws.result.repairs[-1].tlc_passed = False
            ws.result.repairs[-1].new_violation_type = "pcal_error"

        error_text = f"# PlusCal Translation Error\n\n{pcal_result.error_message}"

        # Supplementary diagnostic: pcal.trans gives a single, often-cryptic
        # parser position ("Expected '}' but found X"). Run our own lint
        # against the same content to surface structural issues (like a
        # brace-count imbalance) that are easier to act on directly than
        # tracing nested braces backward from a parser error.
        supplementary = _lint_pluscal_all(tla_content)
        if supplementary:
            error_text += (
                "\n\n---\n\n"
                "## Supplementary diagnostic (independent of pcal.trans)\n\n"
                "The checks below scan the file directly and may point to the "
                "root cause of the error above more precisely:\n\n"
                f"{supplementary}"
            )

        ws.write_file("tlc_error.md", error_text)
        ws._backup_protocol_attempt()
        result_text = f"FAIL \u2014 PlusCal syntax error:\n{pcal_result.error_message}"
        if supplementary:
            result_text += (
                "\n\nSupplementary diagnostic (see tlc_error.md for full detail): "
                f"{supplementary}"
            )
        return result_text

    # Save translated TLA+ for extract_states (Phase 4)
    ws.write_file("Protocol_translated.tla", pcal_result.translated_tla)

    # Step 5: Run TLC on translated spec
    tlc_result = _run_tlc(pcal_result.translated_tla, cfg_content, timeout=timeout)
    ws.write_file("tlc_output.log", tlc_result.raw_output)

    ws.result.tla_compiled = True
    ws.result.tla_lines = len(pcal_result.translated_tla.split("\n"))

    stats = tlc_result.stats
    ws.result.tlc_states_generated = stats.get("states_generated", 0)
    ws.result.tlc_distinct_states = stats.get("distinct_states", 0)
    ws.result.tlc_elapsed_seconds = stats.get("elapsed_seconds", 0.0)
    ws.result.tlc_violation_type = tlc_result.violation_type or ""

    if tlc_result.success:
        ws.result.tlc_status = "pass"
        ws.result.final_passed = True
        ws.result.passed_at_repair = len(ws.result.repairs)
        if ws.result.repairs:
            ws.result.repairs[-1].tlc_passed = True
        error_path = ws.path("tlc_error.md")
        if error_path.exists():
            error_path.unlink()
        return (
            f"PASS. PlusCal translated ({ws.result.tla_lines} lines TLA+). "
            f"TLC: {stats.get('states_generated', '?')} states generated, "
            f"{stats.get('distinct_states', '?')} distinct. "
            f"Time: {stats.get('elapsed_seconds', '?')}s."
        )

    # Failed
    ws.result.tlc_status = "fail"
    if ws.result.repairs:
        ws.result.repairs[-1].tlc_passed = False
        ws.result.repairs[-1].new_violation_type = tlc_result.violation_type or ""

    trace = parse_trace(tlc_result.raw_output)
    error_formatted = format_tlc_error(tlc_result, trace)
    ws.write_file("tlc_error.md", error_formatted)
    ws._backup_protocol_attempt()

    header = (
        f"FAIL \u2014 {tlc_result.violation_type or 'unknown'} violation. "
        f"PlusCal translated ({ws.result.tla_lines} lines).\n\n"
    )
    max_error_chars = 1800 - len(header)
    if len(error_formatted) > max_error_chars:
        error_inline = error_formatted[:max_error_chars] + "\n... (truncated, see tlc_error.md for full trace)"
    else:
        error_inline = error_formatted
    return header + error_inline


# ---------------------------------------------------------------------------
# State extraction tools
# ---------------------------------------------------------------------------


def extract_states(ws: Workspace) -> str:
    """Extract per-agent state machine from verified Protocol_translated.tla."""
    import json as _json
    from tracefix.pipeline.pipeline.pluscal_parser import (
        parse_pluscal,
        lint_adjacent_acquire_release,
    )

    translated = ws.read_file("Protocol_translated.tla")
    if translated is None:
        return "ERROR: Protocol_translated.tla not found. Call verify_spec first (must PASS)."

    ir_data = ws.read_ir()
    if ir_data is None:
        return "ERROR: No valid ir.json in workspace."

    result = parse_pluscal(translated, ir_data)

    # Annotate tool_hints (same logic as cli.py _annotate_tool_hints)
    for state in result.states:
        actions = state.get("actions", [])
        if len(actions) <= 1:
            continue
        has_recv = [bool(a.get("receive")) for a in actions]
        if all(has_recv):
            state["tool_hint"] = "receive_any"
        elif any(has_recv):
            state["tool_hint"] = "poll_channels"

    lint_warnings = lint_adjacent_acquire_release(result.states)

    out_data = {"states": result.states, "initial_states": result.initial_states}
    if result.local_variables:
        out_data["local_variables"] = result.local_variables
    ws.write_file("states.json", _json.dumps(out_data, indent=2))

    n_actions = sum(len(s.get("actions", [])) for s in result.states)
    n_terminal = sum(1 for s in result.states if not s.get("actions"))
    parts = [f"OK — wrote states.json. {len(result.states)} states, {n_actions} actions, {n_terminal} terminal."]
    if result.errors:
        parts.append(f"WARNING: {len(result.errors)} parse error(s):")
        parts.extend(f"  - {e}" for e in result.errors)
    if lint_warnings:
        parts.append(f"LINT: {len(lint_warnings)} work-state warning(s):")
        parts.extend(f"  - {w}" for w in lint_warnings)
    parts.append("Next: read states.json, then generate per-agent prompts (Phase 4).")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Workflow tools
# ---------------------------------------------------------------------------


def load_benchmark(ws: Workspace, *, task_id: str) -> str:
    """Load a benchmark task by ID.

    Copies description.md -> task.md, and tools.json/metadata.json verbatim
    (preserving skill-compatible naming). tools.json and metadata.json must be
    referenced by those exact names in later phases.
    """
    from pathlib import Path
    from benchmark.loader import load_task

    try:
        task = load_task(task_id)
    except ValueError as e:
        return f"ERROR: {e}"

    # Write task description to workspace
    ws.write_file("task.md", task.description)

    _repo_root = next(p for p in Path(__file__).resolve().parents if (p / "pyproject.toml").exists())
    desc_dir = _repo_root / "benchmark" / "descriptions" / task.task_id
    extras: list[str] = []

    # Copy tools.json (per-agent domain tool schemas)
    tools_path = desc_dir / "tools.json"
    if tools_path.exists():
        tools_content = tools_path.read_text(encoding="utf-8")
        ws.write_file("tools.json", tools_content)
        try:
            tools_data = json.loads(tools_content)
            extras.append(f"tools.json: {len(tools_data)} domain tool schema(s)")
        except json.JSONDecodeError:
            extras.append("tools.json: copied (unparsed)")

    # Copy metadata.json (canonical agent/resource IDs)
    metadata_path = desc_dir / "metadata.json"
    if metadata_path.exists():
        metadata_content = metadata_path.read_text(encoding="utf-8")
        ws.write_file("metadata.json", metadata_content)
        extras.append("metadata.json: canonical naming source")

    extras_msg = "\n  " + "\n  ".join(extras) if extras else ""

    return (
        f"Loaded task: {task.task_name}\n"
        f"  ID: {task.task_id}\n"
        f"  Scenario: {task.scenario}, Difficulty: {task.difficulty}\n"
        f"  Description written to task.md ({len(task.description)} chars)."
        + extras_msg
    )


# ---------------------------------------------------------------------------
# Tool registry: maps tool name -> callable
# ---------------------------------------------------------------------------

TOOL_REGISTRY: dict[str, Callable[..., str]] = {
    "think": think,
    "write_file": write_file,
    "edit_file": edit_file,
    "read_file": read_file,
    "list_files": list_files,
    "validate_ir": validate_ir,
    "compile_scaffold": compile_scaffold,
    "verify_spec": verify_spec,
    "extract_states": extract_states,
    "load_benchmark": load_benchmark,
}
