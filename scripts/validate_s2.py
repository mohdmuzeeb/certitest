"""Validate S2: the hidden oracle passes on the reference and kills every seeded fault."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from subjects import s2_ledger  # noqa: E402

ok = True
for c in s2_ledger.candidates():
    r = s2_ledger.oracle(c.source)
    good = r["correct"] == (c.label == "correct")
    ok &= good
    print(f"{'ok ' if good else 'BAD'} {c.cand_id:32s} label={c.label:7s} oracle_correct={r['correct']} failing={r['failing'][:3]}")
print("ALL VALID" if ok else "VALIDATION FAILED")
sys.exit(0 if ok else 1)
