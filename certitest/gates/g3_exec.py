"""G3 Execution hygiene: hermetic, repeated execution.

The suite runs ``k`` times on the candidate. A test is *deterministic* if it has
the same outcome in every run; otherwise it is quarantined. Skipped tests are
not admitted (a skipped test verifies nothing). A per-test wall-clock limit is
enforced by the harness conftest so that one hanging test fails instead of
killing the whole run.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .. import sandbox

# Harness-owned conftest: per-test alarm. This file is part of the locked harness (G5).
CONFTEST = '''
import signal, pytest

class _TestTimeout(Exception):
    pass

def _alarm(signum, frame):
    raise _TestTimeout("per-test time limit exceeded")

@pytest.fixture(autouse=True)
def _certitest_time_limit():
    old = signal.signal(signal.SIGALRM, _alarm)
    signal.alarm(5)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, old)
'''

HARNESS_FILES = {"conftest.py": CONFTEST}


def base_name(test_id: str) -> str:
    return test_id.split("[")[0]


@dataclass
class ExecResult:
    collected: bool = True
    runs: int = 0
    outcomes: dict[str, str] = field(default_factory=dict)  # deterministic test -> outcome
    quarantined: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    stdout: str = ""

    def failing_functions(self) -> set[str]:
        return {base_name(t) for t, o in self.outcomes.items() if o in ("failed", "error")}

    def passing_functions(self) -> set[str]:
        """Functions all of whose (parametrized) instances deterministically passed."""
        fails = self.failing_functions()
        return {base_name(t) for t, o in self.outcomes.items() if o == "passed"} - fails - {base_name(q) for q in self.quarantined}


def run(module_name: str, candidate_src: str, test_src: str, k: int = 5, extra_files: dict[str, str] | None = None, timeout: float = 120.0) -> ExecResult:
    files = {**HARNESS_FILES, **(extra_files or {}), f"{module_name}.py": candidate_src, "test_generated.py": test_src}
    runs = sandbox.run_many([files] * k, timeout=timeout, workers=min(k, 5))
    res = ExecResult(runs=k)
    if any(not r.collected for r in runs) or all(r.timed_out for r in runs):
        res.collected = False
        res.stdout = runs[0].stdout + runs[0].stderr
        return res
    valid = [r for r in runs if not r.timed_out]
    names = set().union(*(r.outcomes.keys() for r in valid))
    for n in sorted(names):
        seen = {r.outcomes.get(n, "missing") for r in valid}
        if len(valid) < k:
            seen.add("timeout")
        if len(seen) > 1:
            res.quarantined.append(n)
        else:
            o = seen.pop()
            if o == "skipped":
                res.skipped.append(n)
            else:
                res.outcomes[n] = o
    res.stdout = valid[0].stdout if valid else ""
    return res
