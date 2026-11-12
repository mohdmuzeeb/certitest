"""Run CertiTest conditions on a subject and append one JSON row per run.

Examples
  python scripts/run_experiment.py --subject s2 --model anthropic:claude-opus-5-5 --conditions C0,C6 --replicates 1
  python scripts/run_experiment.py --subject s1 --model anthropic:claude-opus-5-5 --conditions all --limit-tasks 40
  python scripts/run_experiment.py --subject s3 --model anthropic:claude-opus-5-5 --replicates 3

Rows already present in the output file are skipped, so interrupted runs resume.
All LLM responses are cached on disk (.cache/llm), so re-running is free.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from certitest import integrity_probe, pipeline  # noqa: E402
from certitest.llm import make_llm  # noqa: E402


def key_of(r: dict) -> tuple:
    if r["subject"] == "s3":
        return ("s3", r["probe_id"], r["mode"], r["model"], r["replicate"])
    return (r["subject"], r["task_id"], r["cand_id"], r["condition"], r["model"], r["replicate"])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--subject", required=True, choices=["s1", "s2", "s3"])
    ap.add_argument("--model", required=True, help="anthropic:<model-id> or ollama:<model>")
    ap.add_argument("--conditions", default="C0,C0b,C6", help="comma list or 'all' (ignored for s3)")
    ap.add_argument("--replicates", type=int, default=3)
    ap.add_argument("--limit-tasks", type=int, default=None, help="S1: deterministic random sample of tasks")
    ap.add_argument("--modes", default="open,locked", help="S3 modes")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--mutants", type=int, default=30)
    ap.add_argument("--out", default=None)
    ap.add_argument("--dry-run", action="store_true", help="print the job count and exit")
    a = ap.parse_args()

    llm = make_llm(a.model)
    out = Path(a.out or ROOT / "results" / "runs" / f"{a.subject}_{a.model.replace(':', '_').replace('/', '_')}.jsonl")
    out.parent.mkdir(parents=True, exist_ok=True)
    done = set()
    if out.exists():
        for line in out.read_text().splitlines():
            try:
                done.add(key_of(json.loads(line)))
            except Exception:
                pass

    jobs = []
    if a.subject == "s3":
        from subjects import s1_humaneval as s1, s3_probes

        raw = {d["task_id"]: d for d in s1.load_raw()}
        for p in s3_probes.load():
            for mode in a.modes.split(","):
                for r in range(a.replicates):
                    if ("s3", p["probe_id"], mode, llm.label, r) in done:
                        continue
                    d = raw[p["task_id"]]
                    jobs.append(lambda p=p, mode=mode, r=r, d=d: integrity_probe.run(
                        p, llm, mode, replicate=r, gt_fn=lambda src, d=d: s1.ground_truth(d, src)))
    else:
        conds = list(pipeline.CONDITIONS) if a.conditions == "all" else a.conditions.split(",")
        if a.subject == "s1":
            from subjects import s1_humaneval as s1

            tasks = {t.task_id: t for t in s1.tasks()}
            cands = s1.load_candidates()
            if not cands:
                sys.exit("No S1 candidates: run scripts/build_s1_candidates.py first")
            tids = sorted(t for t in cands if t in tasks)
            if a.limit_tasks:
                # prefer tasks that have natural (LLM-written) faults, then fill with the rest
                rng = random.Random(2027)
                nat = [t for t in tids if any(c.origin == "llm-faulty" for c in cands[t])]
                rest = [t for t in tids if t not in nat]
                rng.shuffle(nat), rng.shuffle(rest)
                tids = sorted((nat + rest)[: a.limit_tasks])
            pairs = [(tasks[t], c) for t in tids for c in cands[t]]
        else:
            from subjects import s2_ledger

            t = s2_ledger.task()
            pairs = [(t, c) for c in s2_ledger.candidates()]
        for cname in conds:
            cond = pipeline.CONDITIONS[cname]
            for task, cand in pairs:
                for r in range(a.replicates):
                    if (a.subject, task.task_id, cand.cand_id, cname, llm.label, r) in done:
                        continue
                    jobs.append(lambda task=task, cand=cand, cond=cond, r=r: pipeline.run(
                        task, cand, cond, llm, replicate=r, mutant_limit=a.mutants, issue_certificate=cond.name == "C6"))

    print(f"{len(jobs)} jobs to run ({len(done)} already done) -> {out}", flush=True)
    if a.dry_run or not jobs:
        return
    lock = threading.Lock()
    n_ok = n_err = 0
    with ThreadPoolExecutor(max_workers=a.workers) as ex, out.open("a") as f:
        futs = [ex.submit(j) for j in jobs]
        for i, fut in enumerate(as_completed(futs), 1):
            try:
                row = fut.result()
                with lock:
                    f.write(json.dumps(row) + "\n")
                    f.flush()
                n_ok += 1
            except Exception as e:
                n_err += 1
                traceback.print_exc()
                msg = str(e).lower()
                if any(s in msg for s in ("credit balance", "usage limit", "authentication", "invalid x-api-key", "permission")):
                    print(f"FATAL: account/billing error, stopping: {e}", flush=True)
                    for fu in futs:
                        fu.cancel()
                    ex.shutdown(wait=False, cancel_futures=True)
                    sys.exit(3)
            if i % 10 == 0 or i == len(jobs):
                print(f"{i}/{len(jobs)} done ({n_err} errors)", flush=True)


if __name__ == "__main__":
    main()
