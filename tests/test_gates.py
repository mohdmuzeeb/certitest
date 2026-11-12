import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from certitest import mutate, pipeline  # noqa: E402
from certitest.gates import g1_trace, g3_exec, g4_oracle, g5_integrity, g6_certificate  # noqa: E402
from certitest.llm import LLM, Completion  # noqa: E402
from certitest.model import Candidate, Requirement, Task  # noqa: E402

IMPL = "def add(a, b):\n    if a < 0:\n        return -1\n    return a + b\n"
IMPL_BUG = "def add(a, b):\n    if a < 0:\n        return -1\n    return a - b\n"

STRONG = '''from solution import add

def test_basic():
    """REQ: R1"""
    assert add(2, 3) == 5

def test_negative():
    """REQ: R2"""
    assert add(-1, 3) == -1
    assert add(0, 0) == 0
    assert add(1, 0) == 1
'''

WEAK = '''from solution import add

def test_runs():
    """REQ: R1, R2"""
    assert isinstance(add(2, 3), int)

def test_no_assert():
    """REQ: R1"""
    add(1, 2)

def test_untraced():
    assert add(1, 1) == 2
'''

TASK = Task("t", "add", "solution", "add", "add two non-negative ints; negative a returns -1", "def add(a, b): ...",
            [Requirement("R1", "returns a + b"), Requirement("R2", "returns -1 when a < 0")])


def test_g1_trace_and_coverage():
    tr = g1_trace.check(WEAK, ["R1", "R2"])
    assert tr.untraced == ["test_untraced"]
    assert tr.trace["test_runs"] == ["R1", "R2"]
    assert tr.coverage(2) == 1.0
    assert g1_trace.check(WEAK, ["R1", "R2"], admitted={"test_no_assert"}).uncovered == ["R2"]


def test_g4_lint():
    issues = g4_oracle.lint(WEAK)
    assert issues["test_runs"] == ["type-only"]
    assert issues["test_no_assert"] == ["no-assertion"]
    assert "test_basic" not in g4_oracle.lint(STRONG)
    taut = 'def test_t():\n    x = 1\n    assert x == x\n'
    assert g4_oracle.lint(taut)["test_t"] == ["tautology"]
    swallow = 'def test_s():\n    try:\n        assert 1 == 2\n    except Exception:\n        pass\n'
    assert "swallow" in g4_oracle.lint(swallow)["test_s"]


def test_mutation_operator_families_disjoint():
    assert not set(mutate.GATE_OPS) & set(mutate.HELDOUT_OPS)
    ms = mutate.mutants(IMPL, mutate.GATE_OPS)
    assert {m.op for m in ms} >= {"AOR", "ROR", "CRP"}
    assert all(m.source != IMPL for m in ms)


def test_g3_and_mutation_score():
    ex = g3_exec.run("solution", IMPL, STRONG, k=3)
    assert ex.collected and ex.passing_functions() == {"test_basic", "test_negative"}
    strong = g4_oracle.mutation_score(IMPL, "solution", STRONG, ["test_basic", "test_negative"], g3_exec.HARNESS_FILES)
    weak = g4_oracle.mutation_score(IMPL, "solution", WEAK, ["test_runs"], g3_exec.HARNESS_FILES)
    assert strong.score > weak.score
    assert g3_exec.run("solution", IMPL_BUG, STRONG, k=1).failing_functions() == {"test_basic"}


def test_g3_quarantines_flaky():
    flaky = 'import random\nfrom solution import add\n\ndef test_f():\n    """REQ: R1"""\n    assert random.random() < 0.5\n'
    ex = g3_exec.run("solution", IMPL, flaky, k=8)
    assert "test_f" in ex.quarantined


def test_g5_detectors():
    files = {"solution.py": IMPL, "test_solution.py": STRONG}
    lock = g5_integrity.lock(files)
    assert g5_integrity.check(lock, files, "add", STRONG).ok
    tampered = {**files, "test_solution.py": STRONG.replace("== 5", "== 6")}
    assert g5_integrity.check(lock, tampered).kinds() == {"hash"}
    gamed = {**files, "solution.py": "def add(a, b):\n    if (a, b) == (2, 3):\n        return 5\n    return 0\n"}
    assert "literal" in g5_integrity.check(lock, gamed, "add", STRONG).kinds()
    skip = {**files, "solution.py": "import pytest\npytest.skip('x', allow_module_level=True)\n"}
    assert "pattern" in g5_integrity.check(lock, skip).kinds()


def test_g6_certificate_roundtrip():
    cert = g6_certificate.issue(subject="x", requirements=[{"id": "R1", "text": "t"}], trace={"test_basic": ["R1"]},
                                admitted=["test_basic"], implementation=IMPL, tests=STRONG, gates={}, verdict="CERTIFY", residual={})
    assert cert["requirements"] == {"R1": ["test_basic"]}
    assert g6_certificate.verify(cert, IMPL, STRONG) == (True, "valid")
    assert not g6_certificate.verify(cert, IMPL_BUG, STRONG)[0]
    forged = {**cert, "verdict": "REJECT"}
    assert g6_certificate.verify(forged, IMPL, STRONG) == (False, "bad signature")


class ScriptedLLM(LLM):
    """Returns canned test files in order; no network."""
    provider = "fake"

    def __init__(self, replies):
        super().__init__("scripted")
        self.replies = list(replies)

    def complete(self, system, messages, replicate=0, usage=None, tag=""):
        c = Completion(f"```python\n{self.replies.pop(0)}```", 10, 10, 0.0)
        if usage is not None:
            usage.add(c, tag)
        return c


def test_pipeline_verdicts():
    ok = Candidate("ok", "add", IMPL, "correct", "canonical")
    bug = Candidate("bug", "add", IMPL_BUG, "faulty", "seeded")
    c6 = pipeline.CONDITIONS["C6"]
    # strong suite certifies the correct candidate and rejects the faulty one
    assert pipeline.run(TASK, ok, c6, ScriptedLLM([STRONG]), mutant_limit=10)["verdict"] == "CERTIFY"
    assert pipeline.run(TASK, bug, c6, ScriptedLLM([STRONG]))["verdict"] == "REJECT"
    # weak suite: ungated C0 ships the faulty candidate; C6 withholds after repair rounds
    weak_only = WEAK.replace("def test_untraced():\n    assert add(1, 1) == 2\n", "")
    assert pipeline.run(TASK, bug, pipeline.CONDITIONS["C0"], ScriptedLLM([weak_only]))["verdict"] == "ACCEPT"
    r = pipeline.run(TASK, bug, c6, ScriptedLLM([weak_only] * 3))
    assert r["verdict"] == "WITHHOLD" and r["rounds"] == 3 and not r["ships"]
    # a broken file is repaired once, then certifies
    assert pipeline.run(TASK, ok, c6, ScriptedLLM(["this is not python(", STRONG]), mutant_limit=10)["verdict"] == "CERTIFY"
