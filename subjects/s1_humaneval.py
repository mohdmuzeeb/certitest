"""S1: specification-based tasks from HumanEval+ (EvalPlus).

* The specification is the HumanEval prompt (signature + docstring).
* Requirements are extracted deterministically from the docstring: prose
  sentences become R1..Rn, worked examples become E1..Em.
* Ground truth is differential: a candidate is correct iff it matches the
  canonical solution on the EvalPlus base + plus inputs (capped), with the
  task's float tolerance. Ground truth is never visible to agents or gates.
"""
from __future__ import annotations

import gzip
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from certitest.model import Candidate, Requirement, Task  # noqa: E402

RAW = ROOT / "data" / "raw" / "HumanEvalPlus.jsonl.gz"
CANDIDATES = ROOT / "data" / "s1_candidates.jsonl"
EXCLUDE = {"HumanEval/32"}  # find_zero: oracle is a tolerance check on a polynomial root, not output equality
MAX_INPUTS = 200


def load_raw() -> list[dict]:
    with gzip.open(RAW, "rt") as f:
        return [json.loads(l) for l in f if l.strip()]


def _docstring(prompt: str, entry: str) -> tuple[str, str]:
    """Return (signature_line(s), docstring) for the entry-point function."""
    m = re.search(rf"(def {re.escape(entry)}\(.*?\).*?:)\s*\n\s*(\"\"\"|''')(.*?)(\2)", prompt, re.S)
    if not m:
        return f"def {entry}(...):", ""
    return m.group(1), m.group(3)


def extract_requirements(prompt: str, entry: str) -> list[Requirement]:
    _, doc = _docstring(prompt, entry)
    lines = doc.splitlines()
    examples, prose = [], []
    i = 0
    while i < len(lines):
        ln = lines[i].strip()
        if ln.startswith(">>>"):
            call = ln[3:].strip()
            out = lines[i + 1].strip() if i + 1 < len(lines) and not lines[i + 1].strip().startswith(">>>") else ""
            examples.append(f"{call} returns {out}" if out else call)
            i += 2 if out else 1
            continue
        if entry in ln and re.search(r"(==|=>|➞|->|should return|returns?)", ln) and "(" in ln:
            examples.append(ln)
        elif ln:
            prose.append(ln)
        i += 1
    text = " ".join(prose)
    text = re.sub(r"\b(For example|Example|Examples|for example)s?\s*:?\s*$", "", text).strip()
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+(?=[A-Z(])", text) if len(s.strip()) > 3]
    sentences = [s for s in sentences if not re.fullmatch(r"(For example|Examples?|Note)\s*:?", s, re.I)]
    reqs = [Requirement(f"R{k + 1}", s) for k, s in enumerate(sentences)]
    reqs += [Requirement(f"E{k + 1}", e) for k, e in enumerate(examples)]
    if not reqs:
        reqs = [Requirement("R1", f"{entry} behaves as described by its signature and name.")]
    return reqs


def to_task(d: dict) -> Task:
    sig, _ = _docstring(d["prompt"], d["entry_point"])
    return Task(subject="s1", task_id=d["task_id"], module_name="solution", entry_point=d["entry_point"],
                spec=f"```python\n{d['prompt'].rstrip()}\n```", interface=sig,
                requirements=extract_requirements(d["prompt"], d["entry_point"]),
                extra={"atol": d.get("atol", 0)})


def tasks() -> list[Task]:
    return [to_task(d) for d in load_raw() if d["task_id"] not in EXCLUDE]


def canonical_source(d: dict) -> str:
    return d["prompt"] + d["canonical_solution"]


# ---------------------------------------------------------------- ground truth
_GT_SCRIPT = r'''
import json, math, signal, sys
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
import copy
try:
    ref = load(spec["ref"])
except Exception as e:
    print(json.dumps({"error": "ref load " + repr(e)})); sys.exit()
try:
    signal.alarm(5); cand = load(spec["cand"]); signal.alarm(0)
except BaseException as e:
    signal.alarm(0); print(json.dumps({"n": 0, "mismatch": 1, "first": "load: " + repr(e)[:200]})); sys.exit()
n = mism = 0; first = None
for inp in spec["inputs"]:
    try:
        signal.alarm(2); exp = ref(*copy.deepcopy(inp)); signal.alarm(0)
    except BaseException:
        signal.alarm(0); continue
    n += 1
    try:
        signal.alarm(2); got = cand(*copy.deepcopy(inp)); signal.alarm(0)
        ok = eq(got, exp, spec["atol"])
    except BaseException as e:
        signal.alarm(0); ok = False; got = "raised " + type(e).__name__
    if not ok:
        mism += 1
        if first is None: first = {"input": repr(inp)[:200], "expected": repr(exp)[:200], "got": repr(got)[:200]}
print(json.dumps({"n": n, "mismatch": mism, "first": first}))
'''


def ground_truth(d: dict, cand_src: str, max_inputs: int = MAX_INPUTS) -> dict:
    inputs = (d["base_input"] + d["plus_input"])[:max_inputs]
    spec = {"ref": canonical_source(d), "cand": cand_src, "entry": d["entry_point"], "inputs": inputs, "atol": d.get("atol", 0) or 0}
    with tempfile.TemporaryDirectory() as td:
        sp = Path(td) / "spec.json"
        sp.write_text(json.dumps(spec))
        scr = Path(td) / "gt.py"
        scr.write_text(_GT_SCRIPT)
        try:
            p = subprocess.run([sys.executable, "-I", str(scr), str(sp)], capture_output=True, text=True, timeout=300, cwd=td)
            out = json.loads(p.stdout.strip().splitlines()[-1])
        except Exception as e:
            out = {"n": 0, "mismatch": 1, "first": f"harness: {e!r}"[:200]}
    out["correct"] = out.get("mismatch", 1) == 0 and out.get("n", 0) > 0
    return out


def load_candidates() -> dict[str, list[Candidate]]:
    by_task: dict[str, list[Candidate]] = {}
    if not CANDIDATES.exists():
        return by_task
    for line in CANDIDATES.read_text().splitlines():
        c = json.loads(line)
        by_task.setdefault(c["task_id"], []).append(Candidate(c["cand_id"], c["task_id"], c["source"], c["label"], c["origin"], c.get("detail", {})))
    return by_task
