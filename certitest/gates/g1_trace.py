"""G1 Traceability: every admitted test must cite requirement IDs it verifies.

Convention: a test function's docstring contains ``REQ: R1, E2``. Tests with no
valid requirement ID are rejected. Requirement coverage (RC) is the fraction of
requirements traced by at least one admitted test.
"""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field

REQ_RE = re.compile(r"REQ\s*:\s*([A-Za-z]+\d+(?:\s*,\s*[A-Za-z]+\d+)*)")


def find_test_functions(source: str) -> dict[str, ast.FunctionDef]:
    """Top-level ``test_*`` functions and ``test_*`` methods of ``Test*`` classes."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return {}
    out = {}
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name.startswith("test"):
            out[node.name] = node
        elif isinstance(node, ast.ClassDef) and node.name.startswith("Test"):
            for m in node.body:
                if isinstance(m, ast.FunctionDef) and m.name.startswith("test"):
                    out[m.name] = m
    return out


@dataclass
class TraceResult:
    trace: dict[str, list[str]] = field(default_factory=dict)  # test -> requirement IDs
    untraced: list[str] = field(default_factory=list)
    invalid_ids: dict[str, list[str]] = field(default_factory=dict)
    covered: set[str] = field(default_factory=set)
    uncovered: list[str] = field(default_factory=list)

    def coverage(self, n_req: int) -> float:
        return len(self.covered) / n_req if n_req else 1.0


def check(source: str, requirement_ids: list[str], admitted: set[str] | None = None) -> TraceResult:
    valid = set(requirement_ids)
    res = TraceResult()
    for name, fn in find_test_functions(source).items():
        doc = ast.get_docstring(fn) or ""
        ids = []
        for m in REQ_RE.finditer(doc):
            ids += [x.strip().upper() for x in m.group(1).split(",")]
        good = [i for i in ids if i in valid]
        bad = [i for i in ids if i not in valid]
        if bad:
            res.invalid_ids[name] = bad
        if good:
            res.trace[name] = good
        else:
            res.untraced.append(name)
    for t, ids in res.trace.items():
        if admitted is None or t in admitted:
            res.covered.update(ids)
    res.uncovered = [r for r in requirement_ids if r not in res.covered]
    return res
