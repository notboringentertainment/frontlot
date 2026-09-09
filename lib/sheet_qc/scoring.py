"""D19.1 — verdict computation is code, not model (Codex R1#13).

``score(role, answers)`` requires a bijection between the judge's answers and
the authoritative checklist ids: any missing, duplicate or unknown id fails
the verdict with reason ``coverage``. ``unsure`` counts as ``no``. A ``no`` on
a ``fail``-severity item fails; ``warn`` items only populate ``warnings``.
"""
from __future__ import annotations

from typing import Any

from lib.sheet_qc.policy import ANSWERS, checklist


class ScoringError(ValueError):
    pass


def score(role: str, answers: Any) -> dict[str, Any]:
    items = checklist(role)
    expected = [i.id for i in items]
    severity = {i.id: i.severity for i in items}
    if not isinstance(answers, list):
        return _coverage_fail(role, "answers is not a list")
    seen: dict[str, dict[str, Any]] = {}
    problems: list[str] = []
    for raw in answers:
        if not isinstance(raw, dict):
            problems.append("non-object item"); continue
        iid, ans, note = raw.get("id"), raw.get("answer"), raw.get("note")
        if iid not in severity:
            problems.append(f"unknown id {iid!r}"); continue
        if iid in seen:
            problems.append(f"duplicate id {iid!r}"); continue
        if ans not in ANSWERS:
            problems.append(f"bad answer for {iid!r}: {ans!r}"); continue
        seen[iid] = {"id": iid, "answer": ans, "note": str(note or "")[:400], "severity": severity[iid]}
    missing = [i for i in expected if i not in seen]
    if missing:
        problems.append(f"missing ids {missing}")
    if problems:
        return _coverage_fail(role, "; ".join(problems), partial=[seen[i] for i in expected if i in seen])
    normalized = [seen[i] for i in expected]
    failing = [r["id"] for r in normalized if r["severity"] == "fail" and r["answer"] != "yes"]
    warnings = [r["id"] for r in normalized if r["severity"] == "warn" and r["answer"] != "yes"]
    return {"items": normalized, "verdict": "fail" if failing else "pass",
            "failing_items": failing, "warnings": warnings, "coverage_error": None}


def _coverage_fail(role: str, reason: str, partial: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    items = partial or []
    return {"items": items, "verdict": "fail", "failing_items": ["coverage"], "warnings": [],
            "coverage_error": reason}
