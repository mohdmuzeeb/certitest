"""Compute RQ1-RQ4 metrics from run files and write tables.

  FDR  fault detection rate     P(REJECT | faulty)
  FCR  false-confidence rate    P(ships | faulty)       ships = ACCEPT or CERTIFY
  FAR  false-alarm rate         P(REJECT | correct)
  CY   certification yield      P(ships | correct)
  WH   withheld                 P(WITHHOLD or NO_SUITE)

95% CIs: percentile bootstrap resampling tasks (clusters), 2,000 draws.
Paired comparison vs C0 on faulty candidates: exact McNemar on "ships".

Usage: python scripts/analyze.py results/runs/*.jsonl [--out results/summary]
"""
from __future__ import annotations

import argparse
import json
import math
import random
from collections import defaultdict
from pathlib import Path

B = 2000


def load(paths):
    rows = []
    for p in paths:
        for line in Path(p).read_text().splitlines():
            if line.strip():
                rows.append(json.loads(line))
    return rows


def rate(rows, pred):
    return sum(1 for r in rows if pred(r)) / len(rows) if rows else float("nan")


def boot_ci(rows, pred, cluster="task_id", seed=0):
    by = defaultdict(list)
    for r in rows:
        by[r[cluster]].append(r)
    keys = list(by)
    if len(keys) < 2:
        return (float("nan"), float("nan"))
    rng = random.Random(seed)
    vals = []
    for _ in range(B):
        sample = [r for k in (rng.choice(keys) for _ in keys) for r in by[k]]
        vals.append(rate(sample, pred))
    vals.sort()
    return vals[int(0.025 * B)], vals[int(0.975 * B) - 1]


def mcnemar_exact(b, c):
    n = b + c
    if n == 0:
        return 1.0
    k = min(b, c)
    p = sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n
    return min(1.0, 2 * p)


def fmt(x, ci=None, pct=True):
    if x != x:
        return "--"
    s = f"{100 * x:.1f}" if pct else f"{x:.2f}"
    if ci and ci[0] == ci[0]:
        s += f" [{100 * ci[0]:.0f}, {100 * ci[1]:.0f}]" if pct else f" [{ci[0]:.2f}, {ci[1]:.2f}]"
    return s


def mean(xs):
    xs = [x for x in xs if x is not None]
    return sum(xs) / len(xs) if xs else float("nan")


def gate_tables(rows):
    groups = defaultdict(list)
    for r in rows:
        groups[(r["subject"], r["model"], r["condition"])].append(r)
    lines = ["| Subject | Model | Cond. | n(f/c) | FDR | FCR | FAR | CY | WH | RC | MS | calls | out tok | gen s | gate s |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    csv = ["subject,model,condition,n_faulty,n_correct,FDR,FCR,FAR,CY,WH,RC,MS,calls,out_tokens,gen_s,gate_s,mcnemar_p_vs_C0"]
    order = ["C0", "C0b", "C6", "C6-G1", "C6-G2", "C6-G3", "C6-G4"]
    for (subj, model, cond) in sorted(groups, key=lambda k: (k[0], k[1], order.index(k[2]) if k[2] in order else 99)):
        g = groups[(subj, model, cond)]
        f = [r for r in g if r["label"] == "faulty"]
        c = [r for r in g if r["label"] == "correct"]
        fdr, fcr = rate(f, lambda r: r["verdict"] == "REJECT"), rate(f, lambda r: r["ships"])
        far, cy = rate(c, lambda r: r["verdict"] == "REJECT"), rate(c, lambda r: r["ships"])
        wh = rate(g, lambda r: r["verdict"] in ("WITHHOLD", "NO_SUITE"))
        fcr_ci = boot_ci(f, lambda r: r["ships"])
        far_ci = boot_ci(c, lambda r: r["verdict"] == "REJECT")
        # paired McNemar vs C0 on faulty candidates
        p = float("nan")
        base = {(r["task_id"], r["cand_id"], r["replicate"]): r["ships"] for r in groups.get((subj, model, "C0"), []) if r["label"] == "faulty"}
        if cond != "C0" and base:
            pairs = [(base[k], r["ships"]) for r in f if (k := (r["task_id"], r["cand_id"], r["replicate"])) in base]
            b = sum(1 for x, y in pairs if x and not y)
            cc = sum(1 for x, y in pairs if y and not x)
            p = mcnemar_exact(b, cc)
        row = [subj, model.split(":")[-1], cond, f"{len(f)}/{len(c)}", fmt(fdr), fmt(fcr, fcr_ci), fmt(far, far_ci), fmt(cy), fmt(wh),
               fmt(mean(r.get("rc") for r in g), pct=False), fmt(mean(r.get("ms") for r in g), pct=False),
               f"{mean(r['llm_calls'] for r in g):.2f}", f"{mean(r['output_tokens'] for r in g):.0f}",
               f"{mean(r['gen_seconds'] for r in g):.1f}", f"{mean(r['gate_seconds'] for r in g):.1f}"]
        lines.append("| " + " | ".join(row) + (f" (p={p:.3g})" if p == p else "") + " |")
        csv.append(",".join(str(x) for x in [subj, model, cond, len(f), len(c), fdr, fcr, far, cy, wh, mean(r.get("rc") for r in g),
                                              mean(r.get("ms") for r in g), mean(r["llm_calls"] for r in g), mean(r["output_tokens"] for r in g),
                                              mean(r["gen_seconds"] for r in g), mean(r["gate_seconds"] for r in g), p]))
    # breakdown by fault origin
    lines += ["", "False-confidence rate by fault origin", "", "| Subject | Model | Cond. | Origin | n | FCR |", "|---|---|---|---|---|---|"]
    by_origin = defaultdict(list)
    for r in rows:
        if r["label"] == "faulty":
            by_origin[(r["subject"], r["model"], r["condition"], r["origin"])].append(r)
    for k in sorted(by_origin):
        g = by_origin[k]
        lines.append(f"| {k[0]} | {k[1].split(':')[-1]} | {k[2]} | {k[3]} | {len(g)} | {fmt(rate(g, lambda r: r['ships']))} |")
    return lines, csv


def probe_tables(rows):
    groups = defaultdict(list)
    for r in rows:
        groups[(r["model"], r["mode"])].append(r)
    lines = ["| Model | Mode | n | pass on impossible task | via test edit | via spec-violating code | flagged conflict | G5 recall on passes | G5 false flags | detectors |",
             "|---|---|---|---|---|---|---|---|---|---|"]
    for (model, mode), g in sorted(groups.items()):
        cheats = [r for r in g if r["cheated"]]
        honest = [r for r in g if not r["cheated"]]
        det = defaultdict(int)
        for r in cheats:
            for d in r["g5_detectors"]:
                det[d] += 1
        lines.append(f"| {model.split(':')[-1]} | {mode} | {len(g)} | {fmt(rate(g, lambda r: r['cheated']), boot_ci(g, lambda r: r['cheated'], 'probe_id'))} | "
                     f"{fmt(rate(g, lambda r: r['cheated'] and r['tests_modified']))} | {fmt(rate(g, lambda r: r['cheated'] and r['solution_correct'] is False))} | "
                     f"{fmt(rate(g, lambda r: r['flagged_conflict']))} | "
                     f"{fmt(rate(cheats, lambda r: r['g5_flagged']))} ({len(cheats)}) | {fmt(rate(honest, lambda r: r['g5_flagged']))} | {dict(det)} |")
    return lines


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("files", nargs="+")
    ap.add_argument("--out", default="results/summary")
    a = ap.parse_args()
    rows = load(a.files)
    gate_rows = [r for r in rows if r["subject"] in ("s1", "s2")]
    probe_rows = [r for r in rows if r["subject"] == "s3"]
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    md = ["# CertiTest results", ""]
    if gate_rows:
        lines, csv = gate_tables(gate_rows)
        md += ["## RQ1-RQ3: assurance and cost by condition", "", *lines, ""]
        (out / "conditions.csv").write_text("\n".join(csv) + "\n")
    if probe_rows:
        md += ["## RQ4: integrity probes", "", *probe_tables(probe_rows), ""]
    (out / "summary.md").write_text("\n".join(md) + "\n")
    print("\n".join(md))


if __name__ == "__main__":
    main()
