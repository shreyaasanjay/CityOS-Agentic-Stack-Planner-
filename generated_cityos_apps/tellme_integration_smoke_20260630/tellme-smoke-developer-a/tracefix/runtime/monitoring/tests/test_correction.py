"""Tests for the monitoring runtime's correction mechanism (Workstream B).

When correction mode is ON, an out-of-order coordination call is BLOCKED before
any effect and raised as ``StateGuidanceError`` carrying the legal next actions
from ``states.json``. When OFF (the default), behavior is unchanged: the op
proceeds and the violation is soft-recorded.

The core gate is ``StateTracker.check_op`` (snapshot → ``_try_match`` → restore),
so the legality check has zero drift from the committing path.
"""

import pytest

from tracefix.runtime.monitoring.monitor import (
    ProtocolMonitor, ProtocolViolation, StateGuidanceError)
from tracefix.runtime.monitoring.state_tracker import StateTracker
from tracefix.runtime.monitoring.coord import CoordinationContext
from tracefix.runtime.monitoring.correction import (
    corrective_result, describe_hint, CORRECTION_CAP)


def _ir() -> dict:
    return {
        "agents": [{"id": "worker"}, {"id": "peer"}],
        "resources": [{"id": "L", "type": "Lock"}],
        "channels": [
            {"id": "w_to_p", "from": "worker", "to": "peer", "labels": ["go"]},
        ],
    }


def _states() -> dict:
    # worker: w0 --acquire L--> w1 --release L--> w2 --send w_to_p--> w_done
    # peer:   p0 --receive w_to_p--> p_done
    return {
        "initial_states": {"worker": "w0", "peer": "p0"},
        "states": [
            {"id": "w0", "agent": "worker",
             "actions": [{"next_state": "w1", "acquire": "L"}]},
            {"id": "w1", "agent": "worker",
             "actions": [{"next_state": "w2", "release": "L"}]},
            {"id": "w2", "agent": "worker",
             "actions": [{"next_state": "w_done",
                          "send": {"channel": "w_to_p", "label": "go"}}]},
            {"id": "w_done", "agent": "worker", "actions": []},
            {"id": "p0", "agent": "peer",
             "actions": [{"next_state": "p_done",
                          "receive": {"channel": "w_to_p"}}]},
            {"id": "p_done", "agent": "peer", "actions": []},
        ],
    }


# --- core: StateTracker.check_op (sync, read-only) ---------------------------

def test_check_op_blocks_out_of_order_and_is_read_only():
    tr = StateTracker(_states())
    # worker's only legal first op is `acquire L`; a send is out of order.
    ok, legal = tr.check_op("worker", "send", {"channel": "w_to_p", "label": "go"})
    assert ok is False
    assert {"op": "acquire", "resource": "L"} in legal
    # the legal op passes...
    ok2, legal2 = tr.check_op("worker", "acquire", {"resource": "L"})
    assert ok2 is True and legal2 == []
    # ...and check_op is read-only: position did not advance, so `acquire` is
    # STILL the legal next op (zero drift from the snapshot/restore).
    ok3, _ = tr.check_op("worker", "acquire", {"resource": "L"})
    assert ok3 is True


def test_legal_actions_terminal_is_done():
    tr = StateTracker(_states())
    tr._current["peer"] = "p_done"  # terminal: no outgoing actions
    assert tr.legal_actions("peer") == [{"op": "done"}]


# --- integration: coord guard (async) ---------------------------------------

@pytest.mark.asyncio
async def test_correction_on_blocks_and_guides():
    ir, states = _ir(), _states()
    ctx = CoordinationContext(ir, ProtocolMonitor(ir),
                              tracker=StateTracker(states), correction=True)
    # worker tries to send before acquiring -> blocked with guidance.
    with pytest.raises(StateGuidanceError) as ei:
        await ctx.send("w_to_p", "go", "worker")
    err = ei.value
    assert isinstance(err, ProtocolViolation)  # still catchable as the base type
    assert {"op": "acquire", "resource": "L"} in err.legal_actions
    # BLOCKED PRE-EFFECT: the message was never enqueued.
    assert ctx.messages.peek("w_to_p", "go") is False
    # the legal op then succeeds.
    res = await ctx.acquire_lock("L", "worker")
    assert res["status"] == "acquired"


@pytest.mark.asyncio
async def test_correction_off_is_soft_default():
    ir, states = _ir(), _states()
    # default correction=False -> legacy behavior: out-of-order op is NOT blocked.
    ctx = CoordinationContext(ir, ProtocolMonitor(ir), tracker=StateTracker(states))
    res = await ctx.send("w_to_p", "go", "worker")
    assert res["status"] == "sent"
    assert ctx.messages.peek("w_to_p", "go") is True  # effect happened (soft-recorded)


# --- corrective-result formatting -------------------------------------------

def test_corrective_result_and_describe_hint():
    assert describe_hint({"op": "acquire", "resource": "L"}) == 'acquire_lock("L")'
    assert describe_hint({"op": "release", "resource": "L"}) == 'release_lock("L")'
    assert describe_hint({"op": "receive", "channel": "w_to_p"}) == 'receive_message("w_to_p")'
    assert describe_hint({"op": "done"}) == "signal_done()"
    out = corrective_result("send", {"channel": "w_to_p"},
                            [{"op": "acquire", "resource": "L"}], attempt=2)
    assert out["status"] == "error" and out["error"] == "out_of_order"
    assert out["correction_attempt"] == 2
    assert 'acquire_lock("L")' in out["message"]
    assert out["legal_actions"] == [{"op": "acquire", "resource": "L"}]


def test_correction_cap_is_bounded():
    assert isinstance(CORRECTION_CAP, int) and CORRECTION_CAP >= 1
