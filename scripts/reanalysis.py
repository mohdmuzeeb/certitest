"""Revision analyses for the paper (no model calls; reads frozen run records).

  1. Run / call accounting and timing definitions
  2. Paired McNemar tables (discordant counts) for S1 and S2
  3. S1 sensitivity: performance-only and specification-ambiguous labels
  4. G4 zero-mutant sensitivity: verdicts under an "inconclusive" policy
  5. S3 paired open-vs-locked analysis (run level and probe level)
  6. S3 G5 detector breakdown with explicit denominators

Writes results/summary/reanalysis.json and prints a report.
"""
from __future__ import annotations

import json
import math
import random
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNS = ROOT / "results/runs"
L = lambda f: [json.loads(l) for l in open(f) if l.strip()]
s1 = L(RUNS / "s1_anthropic_claude-opus-5-5.jsonl")
s2 = L(RUNS / "s2_anthropic_claude-opus-5-5.jsonl")
s3 = L(RUNS / "s3_anthropic_claude-opus-5-5.jsonl")
audit = json.loads((ROOT / "results/summary/label_audit.json").read_text())
out: dict = {}


def mcnemar_exact(b: int, c: int) -> float:
    n = b + c
    if n == 0:
        return 1.0
    return min(1.0, 2 * sum(math.comb(n, i) for i in range(min(b, c) + 1)) / 2 ** n)


def boot(rows, pred, key, B=2000, seed=0):
    by = defaultdict(list)
    for r in rows:
        by[r[key]].append(r)
    ks = list(by)
    rng = random.Random(seed)
    v = []
    for _ in range(B):
        smp = [r for k in (rng.choice(ks) for _ in ks) for r in by[k]]
        v.append(sum(pred(r) for r in smp) / len(smp))
    v.sort()
    return v[int(0.025 * B)], v[int(0.975 * B) - 1]


# 1. accounting -------------------------------------------------------------------
out["runs"] = {"s1": len(s1), "s2": len(s2), "s3": len(s3), "total": len(s1) + len(s2) + len(s3)}
out["llm_calls_summed_over_runs"] = sum(r["llm_calls"] for r in s1 + s2 + s3)
timing = {}
for subj, rows in (("s1", s1), ("s2", s2)):
    for c in sorted({r["condition"] for r in rows}):
        g = [r for r in rows if r["condition"] == c]
        gen = sum(r["gen_seconds"] for r in g) / len(g)
        gate = sum(r["gate_seconds"] for r in g) / len(g)
        timing[f"{subj}/{c}"] = {"n": len(g), "gen_s": round(gen, 1), "gate_s": round(gate, 1), "total_s": round(gen + gate, 1),
                                 "calls": round(sum(r["llm_calls"] for r in g) / len(g), 2),
                                 "out_tok": round(sum(r["output_tokens"] for r in g) / len(g))}
out["timing"] = timing


# 2. paired McNemar ---------------------------------------------------------------
def paired(rows, a, b, label="faulty"):
    A = {(r["task_id"], r["cand_id"], r["replicate"]): r["ships"] for r in rows if r["condition"] == a and r["label"] == label}
    pairs = [(A[k], r["ships"]) for r in rows if r["condition"] == b and r["label"] == label
             if (k := (r["task_id"], r["cand_id"], r["replicate"])) in A]
    n10 = sum(1 for x, y in pairs if x and not y)   # a ships, b does not
    n01 = sum(1 for x, y in pairs if y and not x)
    both = sum(1 for x, y in pairs if x and y)
    return {"pairs": len(pairs), "both_ship": both, f"{a}_only": n10, f"{b}_only": n01, "p": mcnemar_exact(n10, n01)}


out["mcnemar"] = {
    "s1_faulty_C0_vs_C6": paired(s1, "C0", "C6"), "s1_faulty_C0b_vs_C6": paired(s1, "C0b", "C6"),
    "s1_faulty_C0_vs_C0b": paired(s1, "C0", "C0b"),
    "s1_correct_C0_vs_C6": paired(s1, "C0", "C6", "correct"), "s1_correct_C0b_vs_C6": paired(s1, "C0b", "C6", "correct"),
    "s2_faulty_C0_vs_C6": paired(s2, "C0", "C6"),
}

# 3. S1 label sensitivity --------------------------------------------------------
perf_only = {k for k, v in audit.items() if v["performance_only"]}
# Specification-ambiguous: every mismatching input lies outside the behaviour the docstring defines
# (manual review of every escaped candidate and its sibling faults in the same task; see paper Sec. VI-B).
SPEC_AMBIGUOUS = {
    "HumanEval/55::llm-faulty-0": "raises for n<=0; docstring defines no behaviour for n<1",
    "HumanEval/55::heldout-1": "returns 1 for n=0; docstring defines no behaviour for n<1",
    "HumanEval/48::llm-faulty-0": "ignores case and punctuation; docstring examples never exercise either",
    "HumanEval/48::llm-faulty-1": "ignores case; docstring examples never exercise it",
}
sens = {}
for c in ("C0", "C0b", "C6"):
    f = [r for r in s1 if r["condition"] == c and r["label"] == "faulty"]
    k = lambda r: f"{r['task_id']}::{r['cand_id']}"
    shipped = sorted(k(r) for r in f if r["ships"])
    f_strict = [r for r in f if k(r) not in perf_only and k(r) not in SPEC_AMBIGUOUS]
    sens[c] = {"benchmark": {"n": len(f), "shipped": len(shipped), "fcr": len(shipped) / len(f)},
               "functional_only": {"n": len([r for r in f if k(r) not in perf_only]),
                                   "shipped": sum(1 for r in f if r["ships"] and k(r) not in perf_only)},
               "spec_aligned": {"n": len(f_strict), "shipped": sum(1 for r in f_strict if r["ships"]),
                                "fcr": sum(1 for r in f_strict if r["ships"]) / len(f_strict)},
               "escapes": shipped}
out["s1_label_sensitivity"] = {"performance_only": sorted(perf_only), "spec_ambiguous": SPEC_AMBIGUOUS, "by_condition": sens}

# 4. zero-mutant sensitivity ------------------------------------------------------
zm = []
for r in s1 + s2:
    if not r["condition"].startswith("C6"):
        continue
    for i, e in enumerate(r["events"]):
        if e.get("mutants") == 0:
            zm.append({"subject": r["subject"], "task": r["task_id"], "cand": r["cand_id"], "label": r["label"],
                       "cond": r["condition"], "round": i, "rc": e.get("rc"), "passing": e.get("passing"),
                       "failing": e.get("failing"), "final_verdict": r["verdict"], "rounds": r["rounds"]})
            break
out["zero_mutant_runs"] = zm
# Under an "inconclusive" policy the first zero-mutant round would end with CERTIFY (flagged inconclusive)
# whenever coverage was complete and admitted tests passed, instead of triggering repair rounds.
flip = [z for z in zm if z["rc"] == 1.0 and (z["passing"] or 0) > 0 and z["failing"] == 0]
out["zero_mutant_policy_changes"] = flip
c6c = [r for r in s1 if r["condition"] == "C6" and r["label"] == "correct"]
c6f = [r for r in s1 if r["condition"] == "C6" and r["label"] == "faulty"]
key = lambda r: (r["task_id"], r["cand_id"])
fl = {(z["task"], z["cand"]) for z in flip if z["subject"] == "s1" and z["cond"] == "C6"}
out["s1_C6_under_inconclusive_policy"] = {
    "CY": (sum(1 for r in c6c if r["ships"] or key(r) in fl), len(c6c)),
    "FCR": (sum(1 for r in c6f if r["ships"] or key(r) in fl), len(c6f)),
}

# 5. S3 paired analysis -----------------------------------------------------------
op = {(r["probe_id"], r["replicate"]): r for r in s3 if r["mode"] == "open"}
lk = {(r["probe_id"], r["replicate"]): r for r in s3 if r["mode"] == "locked"}
pairs = [(op[k]["cheated"], lk[k]["cheated"]) for k in op if k in lk]
b = sum(1 for x, y in pairs if x and not y)
c = sum(1 for x, y in pairs if y and not x)
probe_open = Counter(r["probe_id"] for r in s3 if r["mode"] == "open" and r["cheated"])
probe_lock = Counter(r["probe_id"] for r in s3 if r["mode"] == "locked" and r["cheated"])
probes = sorted({r["probe_id"] for r in s3})
diff = [probe_open[p] - probe_lock[p] for p in probes]
pos, neg = sum(d > 0 for d in diff), sum(d < 0 for d in diff)
out["s3_paired"] = {
    "run_level": {"pairs": len(pairs), "open_only": b, "locked_only": c, "both": sum(1 for x, y in pairs if x and y),
                  "mcnemar_p": mcnemar_exact(b, c)},
    "probe_level_sign_test": {"probes": len(probes), "open_more": pos, "locked_more": neg, "ties": len(probes) - pos - neg,
                              "p": mcnemar_exact(pos, neg)},
    "open_pass_ci_probe_cluster": boot([r for r in s3 if r["mode"] == "open"], lambda r: r["cheated"], "probe_id"),
    "locked_pass_ci_probe_cluster": boot([r for r in s3 if r["mode"] == "locked"], lambda r: r["cheated"], "probe_id"),
}

# 6. S3 detector breakdown ---------------------------------------------------------
det = {}
for m in ("open", "locked"):
    g = [r for r in s3 if r["mode"] == m]
    passes = [r for r in g if r["cheated"]]
    honest = [r for r in g if not r["cheated"]]
    det[m] = {
        "runs": len(g), "passes": len(passes),
        "passes_with_test_edit": sum(1 for r in passes if r["tests_modified"]),
        "passes_code_only": sum(1 for r in passes if not r["tests_modified"]),
        "hash_detected_test_edit_passes": sum(1 for r in passes if "hash" in r["g5_detectors"]),
        "heuristic_detected_code_only_passes": sum(1 for r in passes if not r["tests_modified"] and r["g5_flagged"]),
        "runs_with_attempted_test_edit": sum(1 for r in g if r["attempted_test_edits"]),
        "attempted_edits_total": sum(r["attempted_test_edits"] for r in g),
        "honest_runs": len(honest),
        "honest_flagged_any": sum(1 for r in honest if r["g5_flagged"]),
        "honest_flagged_by_detector": dict(Counter(d for r in honest for d in r["g5_detectors"])),
        "honest_with_hash_change": sum(1 for r in honest if r["tests_modified"]),
        "conflict_keyword_flag": sum(1 for r in g if r["flagged_conflict"]),
        "code_only_pass_solution_correct": [r["solution_correct"] for r in passes if not r["tests_modified"]],
        "code_only_pass_probes": [r["probe_id"] for r in passes if not r["tests_modified"]],
    }
out["s3_detectors"] = det
out["s2_ablation"] = {c: Counter(r["verdict"] for r in s2 if r["condition"] == c) for c in sorted({r["condition"] for r in s2})}
out["gate_activity_s1_C6"] = {k: sum(e.get(k, 0) or 0 for r in s1 if r["condition"] == "C6" for e in r["events"])
                              for k in ("n_tests", "untraced", "lint_rejected", "quarantined", "skipped")}

(ROOT / "results/summary/reanalysis.json").write_text(json.dumps(out, indent=1, default=str))
print(json.dumps(out, indent=1, default=str))
