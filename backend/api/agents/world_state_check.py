"""
world_state_check.py — Phase 3: declarative success-condition checking.

The observation loop the Planning Engine brief asks for ("after each step,
observe the updated World State; if the objective is not achieved, retry")
needs a way to compare a step's *declared* expectation against what actually
changed — without giving a plan the ability to run arbitrary code. This is
intentionally a tiny, pure, side-effect-free structural comparator: no
eval(), no LLM call, no I/O. It reads two World State snapshots
(before/after — see api/services/world_state.py's get_context()) and
answers one yes/no question.

This is deliberately NOT "the Reasoner" — it doesn't interpret meaning, it
checks a specific field against a specific declared expectation. A planner
(LLM or rule-based) decides *what* condition matters for a given step;
this module only ever answers "was that specific condition met".

Condition shape (a plain dict, JSON-serializable so an LLM planner can emit
it directly):
    {"field": "current_document", "op": "changed"}
    {"field": "current_url", "op": "contains", "value": "confirmation"}
    {"field": "current_product", "op": "not_none"}
    {"field": "current_selection", "op": "equals", "value": {...}}

Supported ops: changed, unchanged, not_none, is_none, equals, not_equals, contains.
Dotted field paths are supported for nested dicts (e.g. "current_browser.page_type").

Logs: [WORLD_STATE_CHECK]
"""
from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger("api.agents.world_state_check")

_VALID_OPS = frozenset({"changed", "unchanged", "not_none", "is_none", "equals", "not_equals", "contains"})


def _get_path(snapshot: dict, dotted_field: str) -> Any:
    value: Any = snapshot
    for part in dotted_field.split("."):
        if isinstance(value, dict):
            value = value.get(part)
        else:
            return None
    return value


def check_condition(condition: Optional[dict], before: dict, after: dict) -> tuple[bool, str]:
    """
    Evaluate one declarative condition against before/after World State
    snapshots. Returns (passed, reason). A missing/malformed condition is
    treated as "nothing to check" → passes (never blocks a plan that didn't
    declare a condition, or one written with a typo the LLM planner made).
    """
    if not condition:
        return True, "no condition declared"

    field_path = condition.get("field")
    op = condition.get("op")
    expected = condition.get("value")

    if not field_path or op not in _VALID_OPS:
        logger.debug("[WORLD_STATE_CHECK] malformed condition=%r — treating as pass", condition)
        return True, "malformed condition — skipped"

    before_val = _get_path(before, field_path)
    after_val = _get_path(after, field_path)

    if op == "changed":
        passed = before_val != after_val
    elif op == "unchanged":
        passed = before_val == after_val
    elif op == "not_none":
        passed = after_val is not None
    elif op == "is_none":
        passed = after_val is None
    elif op == "equals":
        passed = after_val == expected
    elif op == "not_equals":
        passed = after_val != expected
    elif op == "contains":
        try:
            passed = expected in after_val if after_val is not None else False
        except TypeError:
            passed = False
    else:  # unreachable given the _VALID_OPS guard above
        passed = True

    reason = f"field={field_path} op={op} before={before_val!r} after={after_val!r} expected={expected!r}"
    logger.info("[WORLD_STATE_CHECK] passed=%s %s", passed, reason)
    return passed, reason
