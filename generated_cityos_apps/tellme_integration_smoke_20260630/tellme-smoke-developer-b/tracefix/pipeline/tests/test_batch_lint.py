"""Regression tests for the batch PlusCal lint checks in tools.py.

These specifically guard against false positives on valid PlusCal (which
would needlessly block a correct spec) and false negatives on the known bug
shapes that have shown up in real agent runs (which would let a broken spec
through to a confusing pcal.trans error).
"""

import json
import tempfile

from tracefix.pipeline.tools import (
    _lint_pluscal_all,
    _scan_brace_balance,
    _scan_label_after_unterminated_brace,
    compile_scaffold,
    write_file,
)
from tracefix.pipeline.workspace import Workspace


def _compile_clean_scaffold() -> str:
    """Compile the 3E fixture scaffold and return its raw TLA+ content."""
    ir = json.load(
        open("tracefix/pipeline/tests/fixtures/3E/ir.json")
    )
    with tempfile.TemporaryDirectory() as d:
        ws = Workspace(session_id="t", base_dir=d)
        write_file(ws, path="ir.json", content=json.dumps(ir))
        compile_scaffold(ws)
        return ws.read_tla()


def test_clean_scaffold_has_no_lint_issues():
    # The last process in a file must not have its own closing brace's
    # surrounding TLA+ content (operator definitions, set literals in
    # invariants, etc.) misattributed to it as an imbalance.
    tla = _compile_clean_scaffold()
    assert _lint_pluscal_all(tla) is None


def test_brace_balance_no_false_positive_on_last_process():
    tla = _compile_clean_scaffold()
    lines = tla.splitlines()
    assert _scan_brace_balance(lines) == []


def test_brace_balance_catches_missing_closing_brace():
    broken = """
fair process (researcherA_proc \\in {ResearcherA})
variables msg = "";
{
  researcherA_start: while (TRUE) {
    rA_research:
      skip;
}

fair process (researcherB_proc \\in {ResearcherB})
variables msg = "";
{
  researcherB_start:
    skip;
}
"""
    issues = _scan_brace_balance(broken.splitlines())
    assert len(issues) == 1
    assert "researcherA_proc" in issues[0][1]


def test_brace_balance_balanced_multi_statement_process_is_clean():
    tla = _compile_clean_scaffold()
    bad_tla = tla.replace(
        "researcherA_start:\n    skip; \\* TODO: replace with researcherA's protocol logic\n}",
        "researcherA_start: while (TRUE) {\n  rA_research:\n    skip;\n  rA_done:\n    skip;\n}",
    )
    assert bad_tla != tla
    assert _lint_pluscal_all(bad_tla) is None


def test_label_after_bare_brace_is_flagged():
    broken_lines = """
fair process (researcherA_proc \\in {ResearcherA})
variables msg = "";
{
  rA_loop: while (TRUE) {
    rA_research:
      skip;
    };
}
rA_done_label:
  skip;
""".splitlines()
    issues = _scan_label_after_unterminated_brace(broken_lines)
    assert len(issues) == 1
    assert "rA_done_label" in issues[0][1]


def test_label_after_terminated_brace_is_not_flagged():
    clean_lines = """
fair process (P \\in {P})
{
  loop: while (TRUE) {
    a:
      skip;
  };
done:
  skip;
}
""".splitlines()
    assert _scan_label_after_unterminated_brace(clean_lines) == []


def test_fair_process_prefix_is_recognized():
    # Regression: _PROCESS_RE originally only matched bare "process (...)"
    # and silently failed to match the actual scaffold syntax "fair process
    # (...)", which meant _scan_brace_balance never fired at all.
    lines = ["fair process (researcherA_proc \\in {ResearcherA})", "{", "}"]
    # No imbalance expected here, but the process must be recognized
    # (verified indirectly: a deliberately broken version below must catch
    # an issue, which requires the process to be matched in the first place).
    assert _scan_brace_balance(lines) == []

    broken_lines = ["fair process (researcherA_proc \\in {ResearcherA})", "{", "{"]
    issues = _scan_brace_balance(broken_lines)
    assert len(issues) == 1
    assert "researcherA_proc" in issues[0][1]
