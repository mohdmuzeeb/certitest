# Supplementary material: CertiTest (IEEE SysCon 2027)

Everything here regenerates from the files in this repository without model access, unless a section says otherwise.

## S-A. How the six gates were evaluated

| Gate | Where it is evaluated | Evidence |
|---|---|---|
| G1–G4 | Condition **C6** on S1 and S2, plus leave-one-out ablations C6−G1 … C6−G4 on S2 | `results/runs/s1_*.jsonl`, `results/runs/s2_*.jsonl` |
| G5 | Separately, in S3 (open = detect, locked = enforce), with no other gates | `results/runs/s3_*.jsonl` |
| G6 | Scripted negative control NC10 only (certificates are also issued in every C6 run) | `tests/test_negative_controls.py`, `examples/certificate_example.json` |

The paper evaluates the components of the six-gate framework one at a time. It does not include an end-to-end deployment of all six gates together.

## S-B. Label sensitivity (paper Table III)

Produced by `scripts/label_audit.py`, which writes `results/summary/label_audit.json`. The audit covers the **90 benchmark-faulty** S1 candidates; the 51 benchmark-correct candidates were not re-audited.

| Task | Candidate | Benchmark label | Observed mismatch (of ≤200 inputs) | Reason for exclusion | Sensitivity label | C0 | C0b | C6 |
|---|---|---|---|---|---|---|---|---|
| HumanEval/55 | llm-faulty-1 | faulty | 19–20 timeouts, no wrong value (exponential recursion) | performance only | excluded | shipped | shipped | shipped |
| HumanEval/63 | llm-faulty-0 | faulty | 34 timeouts, no wrong value | performance only | excluded | rejected | rejected | rejected |
| HumanEval/55 | llm-faulty-0 | faulty | raises `ValueError` for n=0 (inputs `0`, `False`) | n<1 unspecified | excluded | shipped | shipped | shipped |
| HumanEval/55 | heldout-1 | faulty | returns 1 for n=0 (inputs `0`, `False`) | n<1 unspecified | excluded | shipped | shipped | shipped |
| HumanEval/48 | llm-faulty-0 | faulty | ignores case, spaces and punctuation (15 inputs) | case and punctuation unspecified | excluded | shipped | rejected | rejected |
| HumanEval/48 | llm-faulty-1 | faulty | ignores case (2 inputs) | case unspecified | excluded | rejected | rejected | rejected |

Timeout counts vary slightly between machine runs because of the 2 s per-input limit. Excluding a candidate is an alternative classification, not a claim that the candidate is correct. Benchmark labels remain the primary basis for comparison.

## S-C. Negative controls (paper Table IV)

Run with `.venv/bin/python -m pytest -q tests/test_negative_controls.py`. All 10 pass, and the full suite is 18/18.

| ID | Gate | Test function | Injected failure | Expected outcome | Observed |
|---|---|---|---|---|---|
| NC1 | G1 | `test_nc_g1_missing_requirement_id_not_admitted` | `REQ:` tag replaced with plain text | test untraced, R2 uncovered | as expected |
| NC2 | G1 | `test_nc_g1_unknown_requirement_id_does_not_count` | test tags `R9`, which is not a requirement | R2 still uncovered | as expected |
| NC3 | G2 | `test_nc_g2_barrier_excludes_implementation` | build blind and aware prompts | implementation source only in the aware prompt | as expected |
| NC4 | G3 | `test_nc_g3_nondeterministic_test_quarantined` | `assert random.random() < 0.5` | quarantined over k=8 runs | as expected |
| NC5 | G3 | `test_nc_g3_hanging_test_fails_instead_of_passing` | `while True: pass` | fails at the 5 s alarm; never passes | as expected (outcome `failed`) |
| NC6 | G4 | `test_nc_g4_assertion_free_and_tautology_rejected` | no-assertion test; `assert y == y` | lint: `no-assertion`, `tautology` | as expected |
| NC7 | G4 | `test_nc_g4_low_mutation_adequacy_withheld` | only check is `add(5,5) > 0` | WITHHOLD, MS < 0.6 | as expected |
| NC8 | G4 | `test_nc_g4_no_applicable_mutants_is_inconclusive_not_zero` | `def ident(x): return x` (no GATE_OPS mutant) | MS undefined; CERTIFY with `oracle_strength_inconclusive` | as expected |
| NC9 | G5 | `test_nc_g5_unauthorized_test_edit_detected` | `conftest.py` edited; test file deleted | `hash` violation for each | as expected |
| NC10 | G6 | `test_nc_g6_tampered_certificate_and_changed_code_rejected` | verdict, implementation, or tests changed | `bad signature` / `implementation changed` / `tests changed` | as expected |

## S-D. Revised G4 policy re-execution

- **Policy change:** the frozen runs scored "no applicable gate-family mutants" as MS = 0. CertiTest 1.0 reports it as *inconclusive*.
- **What was re-executed:** `scripts/rerun_zero_mutant.py` re-ran the only two affected runs (S1, C6, replicate 0) under the revised code, with the same prompts, model and thresholds.
- **No new model calls:** every model response came from the cache.
  - HumanEval/86 certifies at round 0.
  - HumanEval/12 needs one repair round. Its round-1 prompt is identical to that of HumanEval/12 `canonical`, because G2 hides the implementation, so the cached response was reused.
- **Records:** `results/runs_revised/s1_zero_mutant_C6.jsonl`. Both runs end CERTIFY, with residual risk `oracle_strength_inconclusive: true`.
- **Effect on C6 CY:** 43/51 (84.3%) under the revised policy. Paper Table II keeps the original-policy 80.4%.
- **Why the cache was needed:** without it, the HumanEval/12 repair round would need a new model call, and its outcome could differ.

## S-E. Example certificate

- **File:** `examples/certificate_example.json`, taken from the S2 C6 run on the ledger reference implementation, replicate 0.
- **Contents:** per-requirement test lists, gate results, residual risk, SHA-256 hashes of the tests and implementation, toolchain versions, and an HMAC-SHA256 signature.
- **Verification:** the signature can only be checked with the certifier's key, which is not published. Third-party verification would need an asymmetric key.

## S-F. Statistics

`scripts/reanalysis.py` writes `results/summary/reanalysis.json`.

- **Exact McNemar on "ships":** pairs are matched by (task, candidate, replicate). p-values are approximate because candidates are clustered within tasks.
- **S3 run level:** pairs are matched by (probe, replicate).
- **S3 probe level:** exact sign test on per-probe pass counts, open vs. locked.
- **Confidence intervals:** percentile bootstrap with 2,000 draws, resampling tasks (S1, S2) or probes (S3).
