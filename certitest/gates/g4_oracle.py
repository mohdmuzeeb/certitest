"""G4 Oracle strength: static oracle lint plus mutation adequacy.

Static lint rejects tests whose oracle cannot discriminate behaviour:
  * ``no-assertion``     -- no assert, pytest.raises, or pytest.fail
  * ``tautology``        -- assert on a constant, or both sides structurally identical
  * ``type-only``        -- every assertion only checks type/None-ness
  * ``swallow``          -- broad ``except`` whose body is pass/continue
Mutation adequacy: the mutation score (MS) of the admitted tests over GATE_OPS
mutants of the candidate. A mutant is killed if some test that passes on the
candidate fails (or errors) on the mutant.
"""
from __future__ import annotations

import ast
from dataclasses import dataclass, field

from .. import mutate, sandbox
from .g1_trace import find_test_functions


def _is_raises(node: ast.AST) -> bool:
    return isinstance(node, ast.With) and any(
        isinstance(i.context_expr, ast.Call) and isinstance(i.context_expr.func, ast.Attribute)
        and i.context_expr.func.attr in ("raises", "warns") for i in node.items)


def _is_fail_call(node: ast.AST) -> bool:
    return isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "fail"


def _type_only(a: ast.Assert) -> bool:
    t = a.test
    if isinstance(t, ast.Call) and isinstance(t.func, ast.Name) and t.func.id in ("isinstance", "callable", "hasattr"):
        return True
    if isinstance(t, ast.Compare) and len(t.ops) == 1 and isinstance(t.ops[0], (ast.Is, ast.IsNot)) \
            and isinstance(t.comparators[0], ast.Constant) and t.comparators[0].value is None:
        return True
    return False


def _tautology(a: ast.Assert) -> bool:
    t = a.test
    if isinstance(t, ast.Constant):
        return bool(t.value)
    if isinstance(t, ast.Compare) and len(t.ops) == 1:
        return ast.dump(t.left) == ast.dump(t.comparators[0])
    return False


def lint_function(fn: ast.FunctionDef) -> list[str]:
    issues = []
    asserts = [n for n in ast.walk(fn) if isinstance(n, ast.Assert)]
    raises = any(_is_raises(n) for n in ast.walk(fn))
    fails = any(_is_fail_call(n) for n in ast.walk(fn))
    if not asserts and not raises and not fails:
        issues.append("no-assertion")
    if asserts and all(_tautology(a) for a in asserts) and not raises:
        issues.append("tautology")
    if asserts and all(_type_only(a) or _tautology(a) for a in asserts) and not raises and "tautology" not in issues:
        issues.append("type-only")
    for n in ast.walk(fn):
        if isinstance(n, ast.ExceptHandler):
            broad = n.type is None or (isinstance(n.type, ast.Name) and n.type.id in ("Exception", "BaseException"))
            if broad and all(isinstance(s, (ast.Pass, ast.Continue)) for s in n.body):
                issues.append("swallow")
                break
    return issues


def lint(source: str) -> dict[str, list[str]]:
    """test name -> list of issues (only tests with issues are returned)."""
    return {name: iss for name, fn in find_test_functions(source).items() if (iss := lint_function(fn))}


@dataclass
class MutationResult:
    total: int = 0
    killed: int = 0
    timed_out: int = 0
    survivors: list[dict] = field(default_factory=list)

    @property
    def applicable(self) -> bool:
        """False when no gate-family mutant could be generated: the score is undefined, not zero."""
        return self.total > 0

    @property
    def score(self) -> float | None:
        # Revised Nov 2026 (after the paper's runs): an empty mutant set is "not applicable",
        # reported as None and treated as inconclusive by the pipeline. The frozen runs used 0.0.
        return self.killed / self.total if self.total else None


def mutation_score(candidate_src: str, module_name: str, test_src: str, admitted: list[str], harness_files: dict[str, str],
                   limit: int = 30, seed: int = 0, workers: int = 4, timeout: float = 60.0) -> MutationResult:
    """Run the admitted tests against GATE_OPS mutants of ``candidate_src``."""
    res = MutationResult()
    if not admitted:
        return res
    ms = mutate.mutants(candidate_src, mutate.GATE_OPS, limit=limit, seed=seed)
    res.total = len(ms)
    if not ms:
        return res
    admitted_set = set(admitted)
    files = {**harness_files, "test_generated.py": test_src}
    chunks = [ms[i::workers] for i in range(workers)]
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=workers) as ex:
        parts = list(ex.map(lambda ch: sandbox.run_mutants_batched(files, module_name, [m.source for m in ch]) if ch else [], chunks))
    by_id = {}
    for ch, part in zip(chunks, parts):
        for m, r in zip(ch, part):
            by_id[id(m)] = r
    runs = [by_id[id(m)] for m in ms]
    for m, r in zip(ms, runs):
        if r.timed_out or not r.collected:
            # a mutant that hangs or breaks import is detected by any executing suite
            res.killed += 1
            res.timed_out += int(r.timed_out)
            continue
        base = lambda n: n.split("[")[0]
        killed = any(o in ("failed", "error") and base(t) in admitted_set for t, o in r.outcomes.items())
        if killed:
            res.killed += 1
        else:
            res.survivors.append({"op": m.op, "line": m.lineno})
    return res
