"""Audit S1 ground-truth labels of the faulty candidates used in the experiment.

For every faulty candidate in the evaluated tasks, re-run the differential check
and classify each mismatching input as
  timeout     the candidate exceeded the 2 s per-input limit (performance)
  exception   the candidate raised where the reference returned a value
  value       the candidate returned a different value
A candidate whose mismatches are all timeouts is "performance-only": it may be
functionally correct but too slow for the benchmark's inputs.

Writes results/summary/label_audit.json and prints a per-candidate table.
Usage: python scripts/label_audit.py [results/runs/s1_*.jsonl]
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from subjects import s1_humaneval as s1  # noqa: E402

SCRIPT = r'''
import json, math, signal, sys, copy
spec = json.load(open(sys.argv[1]))
class TO(Exception): pass
def _h(s, f): raise TO()
signal.signal(signal.SIGALRM, _h)
def load(src):
    g = {"__name__": "candidate"}
    exec(compile(src, "<cand>", "exec"), g)
    return g[spec["entry"]]
def eq(a, b, atol):
    if isinstance(a, float) or isinstance(b, float):
        try:
            if isinstance(a, bool) or isinstance(b, bool): return a == b
            return math.isclose(a, b, rel_tol=1e-6, abs_tol=max(atol, 1e-9)) or (math.isnan(a) and math.isnan(b))
        except TypeError:
            return False
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return type(a) == type(b) and len(a) == len(b) and all(eq(x, y, atol) for x, y in zip(a, b))
    if isinstance(a, bool) or isinstance(b, bool):
        return type(a) == type(b) and a == b
    if isinstance(a, int) and isinstance(b, int):
        return a == b
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(eq(a[k], b[k], atol) for k in a)
    return type(a) == type(b) and a == b
ref = load(spec["ref"]); cand = load(spec["cand"])
out = []
for inp in spec["inputs"]:
    try:
        signal.alarm(2); exp = ref(*copy.deepcopy(inp)); signal.alarm(0)
    except BaseException:
        signal.alarm(0); continue
    try:
        signal.alarm(2); got = cand(*copy.deepcopy(inp)); signal.alarm(0)
        if not eq(got, exp, spec["atol"]):
            out.append({"kind": "value", "input": repr(inp)[:80], "expected": repr(exp)[:60], "got": repr(got)[:60]})
    except TO:
        signal.alarm(0); out.append({"kind": "timeout", "input": repr(inp)[:80]})
    except BaseException as e:
        signal.alarm(0); out.append({"kind": "exception", "input": repr(inp)[:80], "got": type(e).__name__})
print(json.dumps(out))
'''


def audit(d: dict, src: str) -> list[dict]:
    inputs = (d["base_input"] + d["plus_input"])[: s1.MAX_INPUTS]
    spec = {"ref": s1.canonical_source(d), "cand": src, "entry": d["entry_point"], "inputs": inputs, "atol": d.get("atol", 0) or 0}
    with tempfile.TemporaryDirectory() as td:
        (Path(td) / "spec.json").write_text(json.dumps(spec))
        (Path(td) / "a.py").write_text(SCRIPT)
        p = subprocess.run([sys.executable, "-I", "a.py", "spec.json"], capture_output=True, text=True, timeout=900, cwd=td)
        return json.loads(p.stdout.strip().splitlines()[-1])


def main() -> None:
    runs = sys.argv[1] if len(sys.argv) > 1 else str(ROOT / "results/runs/s1_anthropic_claude-opus-5-5.jsonl")
    rows = [json.loads(l) for l in open(runs)]
    used = sorted({(r["task_id"], r["cand_id"]) for r in rows if r["label"] == "faulty"})
    raw = {d["task_id"]: d for d in s1.load_raw()}
    cands = {(c.task_id, c.cand_id): c for cs in s1.load_candidates().values() for c in cs}
    report = {}
    for tid, cid in used:
        mm = audit(raw[tid], cands[(tid, cid)].source)
        kinds = sorted({m["kind"] for m in mm})
        report[f"{tid}::{cid}"] = {"n_mismatch": len(mm), "kinds": kinds, "performance_only": kinds == ["timeout"], "examples": mm[:4]}
        print(f"{tid:14s} {cid:14s} mismatches={len(mm):3d} kinds={','.join(kinds) or 'NONE'}")
    out = ROOT / "results/summary/label_audit.json"
    out.write_text(json.dumps(report, indent=1))
    perf = [k for k, v in report.items() if v["performance_only"]]
    none = [k for k, v in report.items() if v["n_mismatch"] == 0]
    print(f"\n{len(report)} faulty candidates; performance-only: {perf}; no mismatch on re-run: {none}")


if __name__ == "__main__":
    main()
