"""AST mutation operators.

Two disjoint operator families are defined so that the faults used to
*evaluate* CertiTest are not the same kind of faults the G4 gate uses to
*measure* oracle strength:

* ``GATE_OPS`` (used by G4): arithmetic operator replacement (AOR), relational
  operator replacement (ROR), integer constant replacement (CRP), boolean
  constant flip (BCR).
* ``HELDOUT_OPS`` (used only to build faulty evaluation variants): logical
  connector replacement (LCR), unary ``not`` removal (UOD), builtin swap (BIS),
  and statement deletion (SDL).
"""
from __future__ import annotations

import ast
import copy
import random
from dataclasses import dataclass

AOR = {ast.Add: ast.Sub, ast.Sub: ast.Add, ast.Mult: ast.FloorDiv, ast.FloorDiv: ast.Mult, ast.Div: ast.Mult, ast.Mod: ast.FloorDiv}
ROR = {ast.Lt: ast.LtE, ast.LtE: ast.Lt, ast.Gt: ast.GtE, ast.GtE: ast.Gt, ast.Eq: ast.NotEq, ast.NotEq: ast.Eq}
BUILTIN_SWAP = {"min": "max", "max": "min", "any": "all", "all": "any", "sorted": "list", "abs": "int"}

GATE_OPS = ("AOR", "ROR", "CRP", "BCR")
HELDOUT_OPS = ("LCR", "UOD", "BIS", "SDL")


@dataclass
class Mutant:
    op: str
    lineno: int
    source: str


def _points(tree: ast.AST, ops: tuple[str, ...]) -> list[tuple[str, int]]:
    """Enumerate (op, node_index) pairs; node_index is the position in ast.walk order."""
    pts = []
    for i, node in enumerate(ast.walk(tree)):
        if "AOR" in ops and isinstance(node, ast.BinOp) and type(node.op) in AOR:
            pts.append(("AOR", i))
        if "ROR" in ops and isinstance(node, ast.Compare) and type(node.ops[0]) in ROR:
            pts.append(("ROR", i))
        if "CRP" in ops and isinstance(node, ast.Constant) and type(node.value) is int:
            pts.append(("CRP", i))
        if "BCR" in ops and isinstance(node, ast.Constant) and type(node.value) is bool:
            pts.append(("BCR", i))
        if "LCR" in ops and isinstance(node, ast.BoolOp):
            pts.append(("LCR", i))
        if "UOD" in ops and isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            pts.append(("UOD", i))
        if "BIS" in ops and isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in BUILTIN_SWAP:
            pts.append(("BIS", i))
        if "SDL" in ops and isinstance(node, (ast.FunctionDef,)):
            # deletable statements inside function bodies (not the only statement, not returns/defs)
            for j, st in enumerate(node.body):
                if len(node.body) > 1 and isinstance(st, (ast.Assign, ast.AugAssign, ast.Expr, ast.If, ast.For, ast.While)) \
                        and not (isinstance(st, ast.Expr) and isinstance(getattr(st, "value", None), ast.Constant)):
                    pts.append((f"SDL:{j}", i))
    return pts


class _UnaryNotRemover(ast.NodeTransformer):
    pass


def _apply(tree: ast.AST, op: str, idx: int) -> ast.AST | None:
    tree = copy.deepcopy(tree)
    nodes = list(ast.walk(tree))
    node = nodes[idx]
    if op == "AOR":
        node.op = AOR[type(node.op)]()
    elif op == "ROR":
        node.ops = [ROR[type(node.ops[0])]()] + node.ops[1:]
    elif op == "CRP":
        node.value = node.value + 1 if node.value != 1 else 0
    elif op == "BCR":
        node.value = not node.value
    elif op == "LCR":
        node.op = ast.Or() if isinstance(node.op, ast.And) else ast.And()
    elif op == "UOD":
        # replace `not x` with `x` by rewriting the parent's reference
        for parent in nodes:
            for fname, value in ast.iter_fields(parent):
                if value is node:
                    setattr(parent, fname, node.operand)
                    return tree
                if isinstance(value, list):
                    for k, v in enumerate(value):
                        if v is node:
                            value[k] = node.operand
                            return tree
        return None
    elif op == "BIS":
        node.func.id = BUILTIN_SWAP[node.func.id]
    elif op.startswith("SDL:"):
        j = int(op.split(":")[1])
        node.body = node.body[:j] + node.body[j + 1:]
        if not node.body:
            return None
    return tree


def mutants(source: str, ops: tuple[str, ...], limit: int | None = None, seed: int = 0, skip_lines: set[int] | None = None) -> list[Mutant]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    base = ast.unparse(tree)
    pts = _points(tree, ops)
    nodes = list(ast.walk(tree))
    out, seen = [], {base}
    for op, idx in pts:
        lineno = getattr(nodes[idx], "lineno", 0)
        if skip_lines and lineno in skip_lines:
            continue
        mt = _apply(tree, op, idx)
        if mt is None:
            continue
        try:
            src = ast.unparse(mt)
            compile(src, "<mutant>", "exec")
        except Exception:
            continue
        if src in seen:
            continue
        seen.add(src)
        out.append(Mutant(op.split(":")[0], lineno, src + "\n"))
    if limit is not None and len(out) > limit:
        rng = random.Random(seed)
        out = rng.sample(out, limit)
    return out
