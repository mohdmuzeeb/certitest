"""Re-run the S1 C6 cases that had zero applicable mutants, under the revised G4 policy.

The frozen runs (results/runs/) scored "no applicable mutants" as MS = 0, which withheld
two correct candidates. CertiTest 1.0 treats that case as inconclusive. This script
re-executes exactly those two (task, candidate) pairs with the same prompts, model,
replicate, and thresholds; only the G4 policy differs. Cached responses are reused
where the prompt is unchanged, so new API calls occur only where the repair
conversation diverges from the frozen run.

Output: results/runs_revised/s1_zero_mutant_C6.jsonl (the frozen records are untouched).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from certitest import __version__, pipeline  # noqa: E402
from certitest.llm import make_llm  # noqa: E402
from subjects import s1_humaneval as s1  # noqa: E402

CASES = [("HumanEval/12", "llm-correct-0"), ("HumanEval/86", "canonical")]


def main() -> None:
    llm = make_llm("anthropic:claude-opus-5-5")
    tasks = {t.task_id: t for t in s1.tasks()}
    cands = s1.load_candidates()
    out = ROOT / "results/runs_revised/s1_zero_mutant_C6.jsonl"
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w") as f:
        for tid, cid in CASES:
            cand = next(c for c in cands[tid] if c.cand_id == cid)
            r = pipeline.run(tasks[tid], cand, pipeline.CONDITIONS["C6"], llm, replicate=0, mutant_limit=30,
                             issue_certificate=True)
            r["certitest_version"] = __version__
            r["g4_policy"] = "no-applicable-mutants=inconclusive"
            f.write(json.dumps(r) + "\n")
            print(tid, cid, r["verdict"], "rounds", r["rounds"], "calls", r["llm_calls"],
                  "ms", r["ms"], "inconclusive", r.get("ms_inconclusive"),
                  [(e.get("rc"), e.get("ms"), e.get("mutants"), e.get("failing")) for e in r["events"]])


if __name__ == "__main__":
    main()
