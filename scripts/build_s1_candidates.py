"""Build the S1 candidate pool: correct and faulty implementations per task.

Per task (all labelled by the differential ground-truth oracle):
  * canonical           -- EvalPlus reference (correct by construction)
  * llm-correct  (<=1)  -- an independently generated solution that matches the reference
  * llm-faulty   (<=2)  -- independently generated solutions that do not (realistic agent faults)
  * heldout-mutant (<=2)-- HELDOUT_OPS mutants of the reference that the oracle shows are faulty

Implementations come from small local models so that faults arise naturally;
none of these models is used as a test agent on the same candidate pool.

Usage: python scripts/build_s1_candidates.py [--models ollama:llama3.2:3b,ollama:qwen3:4b] [--samples 3] [--limit N]
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from certitest import mutate  # noqa: E402
from certitest.llm import extract_code, make_llm  # noqa: E402
from subjects import s1_humaneval as s1  # noqa: E402

IMPL_SYSTEM = "You are a Python programmer. Complete the function. Return the complete code, including the function signature, in one ```python block."


def prefix_before_entry(prompt: str, entry: str) -> str:
    idx = prompt.find(f"def {entry}(")
    return prompt[:idx] if idx > 0 else ""


def build_task(d: dict, llms, samples: int, seed: int) -> list[dict]:
    tid, entry = d["task_id"], d["entry_point"]
    out = []
    canon = s1.canonical_source(d)
    gt = s1.ground_truth(d, canon)
    if not gt["correct"]:
        print(f"[warn] canonical fails oracle for {tid}: {gt}", file=sys.stderr)
        return out
    out.append({"cand_id": "canonical", "task_id": tid, "source": canon, "label": "correct", "origin": "canonical", "detail": {}})

    faulty, correct = [], []
    seen = {canon}
    for llm in llms:
        for r in range(samples):
            try:
                comp = llm.complete(IMPL_SYSTEM, [{"role": "user", "content": f"```python\n{d['prompt']}\n```"}], replicate=r)
            except Exception as e:  # model unavailable and response not cached
                print(f"[skip] {tid} sample {r}: {e}", file=sys.stderr)
                continue
            code = extract_code(comp.text)
            if f"def {entry}(" not in code:
                continue
            src = prefix_before_entry(d["prompt"], entry) + code
            if src in seen:
                continue
            seen.add(src)
            g = s1.ground_truth(d, src)
            if g.get("n", 0) == 0:
                continue  # does not even load: not a plausible shipped candidate
            item = {"task_id": tid, "source": src, "detail": {"model": llm.label, "sample": r, "gt": g}}
            (correct if g["correct"] else faulty).append(item)
    rng = random.Random(f"{seed}:{tid}")
    # prefer faulty samples with distinct first-mismatch signatures
    rng.shuffle(faulty)
    picked, sigs = [], set()
    for f in faulty:
        sig = json.dumps(f["detail"]["gt"].get("first"), sort_keys=True)
        if sig not in sigs:
            picked.append(f)
            sigs.add(sig)
        if len(picked) == 2:
            break
    for i, f in enumerate(picked):
        out.append({**f, "cand_id": f"llm-faulty-{i}", "label": "faulty", "origin": "llm-faulty"})
    if correct:
        out.append({**rng.choice(correct), "cand_id": "llm-correct-0", "label": "correct", "origin": "llm-correct"})

    muts = mutate.mutants(canon, mutate.HELDOUT_OPS)
    rng.shuffle(muts)
    kept = 0
    for m in muts:
        g = s1.ground_truth(d, m.source)
        if g.get("n", 0) and not g["correct"]:
            out.append({"cand_id": f"heldout-{kept}", "task_id": tid, "source": m.source, "label": "faulty", "origin": "heldout-mutant",
                        "detail": {"op": m.op, "line": m.lineno, "gt": g}})
            kept += 1
        if kept == 2:
            break
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default="ollama:llama3.2:3b")
    ap.add_argument("--samples", type=int, default=3)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--skip-generation", action="store_true", help="use cached generations only")
    a = ap.parse_args()
    llms = [make_llm(m) for m in a.models.split(",")]
    raw = [d for d in s1.load_raw() if d["task_id"] not in s1.EXCLUDE][: a.limit]
    for llm in ([] if a.skip_generation else llms):  # phase 1: generate per model (no model swapping), cached on disk
        def gen(d, llm=llm):
            for r in range(a.samples):
                llm.complete(IMPL_SYSTEM, [{"role": "user", "content": f"```python\n{d['prompt']}\n```"}], replicate=r)
        with ThreadPoolExecutor(max_workers=a.workers) as ex:
            for i, _ in enumerate(ex.map(gen, raw)):
                if (i + 1) % 20 == 0:
                    print(f"[{llm.label}] generated {i + 1}/{len(raw)}", flush=True)
    rows = []  # phase 2: label with the oracle and select (cache hits only)
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        for i, res in enumerate(ex.map(lambda d: build_task(d, llms, a.samples, a.seed), raw)):
            rows += res
            if (i + 1) % 10 == 0:
                print(f"{i + 1}/{len(raw)} tasks, {len(rows)} candidates", flush=True)
    s1.CANDIDATES.write_text("".join(json.dumps(r) + "\n" for r in rows))
    from collections import Counter
    print("written", s1.CANDIDATES, Counter(r["origin"] for r in rows))


if __name__ == "__main__":
    main()
