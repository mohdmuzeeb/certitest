"""The CertiTest pipeline and the experimental conditions.

Verdicts
  REJECT    some admitted, deterministic test fails on the candidate (defect reported)
  CERTIFY   all admitted tests pass and every enabled gate is satisfied
  ACCEPT    ungated conditions only: all tests pass (no gate evidence required)
  WITHHOLD  all admitted tests pass but a gate is unsatisfied (coverage or oracle strength)
  NO_SUITE  no usable test suite after the repair budget

For safety metrics, ACCEPT and CERTIFY both mean "the candidate would ship".
"""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass

from . import agents
from .gates import g1_trace, g3_exec, g4_oracle, g6_certificate
from .llm import LLM, Usage, extract_code
from .model import Candidate, Task


@dataclass(frozen=True)
class Condition:
    name: str
    barrier: bool = True        # G2: hide implementation from the test agent
    g1: bool = True             # traceability
    k: int = 5                  # G3 reruns (1 = no determinism check)
    lint: bool = True           # G4 static oracle lint
    mutation: bool = True       # G4 mutation adequacy
    tau: float = 0.6            # G4 mutation-score threshold
    gate_rounds: int = 2        # repair rounds driven by gate feedback
    gated: bool = True          # False => ungated verdict (ACCEPT/REJECT)


FIX_ROUNDS = 2  # every condition may repair a suite that fails to collect

CONDITIONS = {
    "C0": Condition("C0", barrier=False, g1=False, k=1, lint=False, mutation=False, gate_rounds=0, gated=False),
    "C0b": Condition("C0b", barrier=True, g1=False, k=1, lint=False, mutation=False, gate_rounds=0, gated=False),
    "C6": Condition("C6"),
    "C6-G1": Condition("C6-G1", g1=False),
    "C6-G2": Condition("C6-G2", barrier=False),
    "C6-G3": Condition("C6-G3", k=1),
    "C6-G4": Condition("C6-G4", lint=False, mutation=False),
}


def run(task: Task, cand: Candidate, cond: Condition, llm: LLM, replicate: int = 0, mutant_limit: int = 30,
        issue_certificate: bool = False) -> dict:
    usage = Usage()
    t_start = time.time()
    gate_seconds = 0.0
    impl = None if cond.barrier else cand.source
    messages = [{"role": "user", "content": agents.build_test_prompt(task, impl)}]
    events = []
    fix_left, gate_left = FIX_ROUNDS, cond.gate_rounds
    record = None

    while True:
        comp = llm.complete(agents.TEST_AGENT_SYSTEM, messages, replicate=replicate, usage=usage, tag=f"gen{len(events)}")
        test_src = extract_code(comp.text)
        messages.append({"role": "assistant", "content": comp.text})
        t0 = time.time()
        ev = {"round": len(events), "n_tests": len(g1_trace.find_test_functions(test_src))}

        # --- G1 + G4 lint: static admission ----------------------------------
        all_tests = set(g1_trace.find_test_functions(test_src))
        trace = g1_trace.check(test_src, task.requirement_ids)
        lint = g4_oracle.lint(test_src) if cond.lint else {}
        admitted = set(all_tests)
        if cond.g1:
            admitted -= set(trace.untraced)
        admitted -= set(lint)
        ev.update(untraced=len(trace.untraced), lint_rejected=len(lint))

        # --- G3: execution hygiene --------------------------------------------
        ex = g3_exec.run(task.module_name, cand.source, test_src, k=cond.k, extra_files=task.extra.get("files"))
        feedback = []
        if not ex.collected or not ex.outcomes:
            gate_seconds += time.time() - t0
            ev.update(collected=False)
            events.append(ev)
            if fix_left > 0:
                fix_left -= 1
                messages.append({"role": "user", "content": agents.feedback_prompt(
                    [f"The test file did not run (collection/import error or no tests). Output:\n{ex.stdout[-1500:]}"])})
                continue
            record = _record(task, cand, cond, llm, replicate, "NO_SUITE", events, usage, t_start, gate_seconds, {})
            break

        quarantined = {g3_exec.base_name(q) for q in ex.quarantined} if cond.k > 1 else set()
        skipped = {g3_exec.base_name(s) for s in ex.skipped}
        admitted -= quarantined | skipped
        executed = {g3_exec.base_name(t) for t in ex.outcomes}
        admitted &= executed
        failing = ex.failing_functions() & admitted
        passing = ex.passing_functions() & admitted
        ev.update(collected=True, quarantined=len(quarantined), skipped=len(skipped), admitted=len(admitted),
                  failing=len(failing), passing=len(passing))

        if failing:
            # A deterministic, traced, well-formed test fails: under G2 this is a reported defect.
            gate_seconds += time.time() - t0
            events.append(ev)
            verdict = "REJECT"
            record = _record(task, cand, cond, llm, replicate, verdict, events, usage, t_start, gate_seconds,
                             {"failing_tests": sorted(failing)[:10]})
            break

        if not cond.gated:
            gate_seconds += time.time() - t0
            events.append(ev)
            verdict = "ACCEPT" if passing else "NO_SUITE"
            record = _record(task, cand, cond, llm, replicate, verdict, events, usage, t_start, gate_seconds, {})
            break

        # --- coverage over admitted, passing tests ----------------------------
        trace_adm = g1_trace.check(test_src, task.requirement_ids, admitted=passing)
        rc = trace_adm.coverage(len(task.requirement_ids))
        ms = None
        mres = None
        if cond.mutation:
            mres = g4_oracle.mutation_score(cand.source, task.module_name, test_src, sorted(passing), g3_exec.HARNESS_FILES,
                                            limit=mutant_limit, seed=replicate)
            ms = mres.score
        gate_seconds += time.time() - t0
        ev.update(rc=round(rc, 4), ms=None if ms is None else round(ms, 4), mutants=None if mres is None else mres.total)
        events.append(ev)

        cov_ok = (rc >= 1.0) if cond.g1 else True
        # No applicable mutants => oracle strength is inconclusive: it neither blocks nor counts as
        # evidence; the certificate records it as residual risk (revised policy, see g4_oracle).
        ms_inconclusive = bool(cond.mutation and mres is not None and not mres.applicable and passing)
        ms_ok = (ms_inconclusive or (ms is not None and ms >= cond.tau)) if cond.mutation else True
        if cov_ok and ms_ok and passing:
            verdict = "CERTIFY"
        else:
            if cond.g1 and trace.untraced:
                feedback.append(f"Tests without a valid `REQ:` docstring were not admitted: {', '.join(sorted(trace.untraced)[:10])}")
            if cond.g1 and not cov_ok:
                miss = [r for r in task.requirements if r.id in trace_adm.uncovered]
                feedback.append("Requirements not verified by any admitted passing test: "
                                + "; ".join(f"{r.id} ({r.text[:120]})" for r in miss[:12]))
            if lint:
                feedback.append("Tests with non-discriminating oracles were not admitted: "
                                + "; ".join(f"{t}: {', '.join(v)}" for t, v in sorted(lint.items())[:10]))
            if quarantined:
                feedback.append(f"Non-deterministic tests were quarantined: {', '.join(sorted(quarantined)[:10])}")
            if cond.mutation and not ms_ok and ms is not None:
                feedback.append(f"Oracle strength too low: the suite detected {mres.killed} of {mres.total} simulated faults "
                                f"(mutation score {ms:.2f} < {cond.tau}). Strengthen assertions: check exact return values, "
                                "boundary values, empty and extreme inputs, and error behaviour for each requirement.")
            if gate_left > 0 and feedback:
                gate_left -= 1
                messages.append({"role": "user", "content": agents.feedback_prompt(feedback)})
                continue
            verdict = "WITHHOLD"

        extra = {"rc": rc, "ms": ms, "ms_inconclusive": ms_inconclusive}
        if issue_certificate:
            extra["certificate"] = g6_certificate.issue(
                subject=f"{task.subject}/{task.task_id}/{cand.cand_id}", requirements=[asdict(r) for r in task.requirements],
                trace=trace_adm.trace, admitted=sorted(passing), implementation=cand.source, tests=test_src,
                gates={"G1": {"rc": rc}, "G3": {"k": cond.k, "quarantined": sorted(quarantined)},
                       "G4": {"lint_rejected": sorted(lint), "mutation_score": ms, "tau": cond.tau}},
                verdict=verdict,
                residual={"uncovered": trace_adm.uncovered, "surviving_mutants": [] if mres is None else mres.survivors[:20],
                          "oracle_strength_inconclusive": ms_inconclusive})
        record = _record(task, cand, cond, llm, replicate, verdict, events, usage, t_start, gate_seconds, extra)
        break

    record["final_tests"] = test_src
    return record


def _record(task, cand, cond, llm, replicate, verdict, events, usage, t_start, gate_seconds, extra) -> dict:
    last = events[-1] if events else {}
    return {
        "subject": task.subject, "task_id": task.task_id, "cand_id": cand.cand_id, "label": cand.label, "origin": cand.origin,
        "condition": cond.name, "model": llm.label, "replicate": replicate, "verdict": verdict,
        "ships": verdict in ("ACCEPT", "CERTIFY"),
        "rounds": len(events), "events": events,
        "rc": extra.get("rc", last.get("rc")), "ms": extra.get("ms", last.get("ms")),
        "llm_calls": usage.calls, "input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens,
        "gen_seconds": round(usage.seconds, 2), "gate_seconds": round(gate_seconds, 2),
        "wall_seconds": round(time.time() - t_start, 2),
        **{k: v for k, v in extra.items() if k not in ("rc", "ms")},
    }
