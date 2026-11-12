"""Gate-level negative controls: each gate must catch the failure it is designed for.

These run without any LLM (scripted test agent) and are reported in the paper.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from certitest import agents, pipeline  # noqa: E402
from certitest.gates import g1_trace, g3_exec, g4_oracle, g5_integrity, g6_certificate  # noqa: E402
from certitest.model import Candidate, Requirement, Task  # noqa: E402
from test_gates import IMPL, IMPL_BUG, STRONG, TASK, ScriptedLLM  # noqa: E402


def test_nc_g1_missing_requirement_id_not_admitted():
    src = STRONG.replace('"""REQ: R2"""', '"""checks negatives"""')
    tr = g1_trace.check(src, ["R1", "R2"])
    assert tr.untraced == ["test_negative"] and tr.uncovered == ["R2"]


def test_nc_g1_unknown_requirement_id_does_not_count():
    src = STRONG.replace('"""REQ: R2"""', '"""REQ: R9"""')
    assert "R2" in g1_trace.check(src, ["R1", "R2"]).uncovered


def test_nc_g2_barrier_excludes_implementation():
    blind = agents.build_test_prompt(TASK, None)
    aware = agents.build_test_prompt(TASK, IMPL)
    assert "return a + b" not in blind and "return a + b" in aware


def test_nc_g3_nondeterministic_test_quarantined():
    flaky = 'import random\nfrom solution import add\n\ndef test_f():\n    """REQ: R1"""\n    assert random.random() < 0.5\n'
    assert "test_f" in g3_exec.run("solution", IMPL, flaky, k=8).quarantined


def test_nc_g3_hanging_test_fails_instead_of_passing():
    hang = 'from solution import add\n\ndef test_h():\n    """REQ: R1"""\n    while True:\n        pass\n'
    ex = g3_exec.run("solution", IMPL, hang, k=1)
    assert "test_h" not in ex.passing_functions()


def test_nc_g4_assertion_free_and_tautology_rejected():
    issues = g4_oracle.lint('def test_a():\n    x = 1\n\ndef test_b():\n    y = 2\n    assert y == y\n')
    assert issues["test_a"] == ["no-assertion"] and issues["test_b"] == ["tautology"]


def test_nc_g4_low_mutation_adequacy_withheld():
    weak = 'from solution import add\n\ndef test_w():\n    """REQ: R1, R2"""\n    assert add(5, 5) > 0\n'
    ok = Candidate("ok", "add", IMPL, "correct", "canonical")
    r = pipeline.run(TASK, ok, pipeline.CONDITIONS["C6"], ScriptedLLM([weak] * 3), mutant_limit=20)
    assert r["verdict"] == "WITHHOLD" and r["ms"] < 0.6


def test_nc_g4_no_applicable_mutants_is_inconclusive_not_zero():
    src = "def ident(x):\n    return x\n"
    tests = 'from solution import ident\n\ndef test_i():\n    """REQ: R1"""\n    assert ident(3) == 3\n'
    res = g4_oracle.mutation_score(src, "solution", tests, ["test_i"], g3_exec.HARNESS_FILES)
    assert res.total == 0 and res.score is None and not res.applicable
    task = Task("t", "ident", "solution", "ident", "return x", "def ident(x): ...", [Requirement("R1", "returns x")])
    r = pipeline.run(task, Candidate("c", "ident", src, "correct", "canonical"), pipeline.CONDITIONS["C6"],
                     ScriptedLLM([tests]), issue_certificate=True)
    assert r["verdict"] == "CERTIFY" and r["ms_inconclusive"]
    assert r["certificate"]["residual_risk"]["oracle_strength_inconclusive"] is True


def test_nc_g5_unauthorized_test_edit_detected():
    files = {"solution.py": IMPL, "test_solution.py": STRONG, "conftest.py": "x = 1\n"}
    lock = g5_integrity.lock(files)
    assert set(lock) == {"test_solution.py", "conftest.py"}
    assert g5_integrity.check(lock, {**files, "conftest.py": "x = 2\n"}).kinds() == {"hash"}
    removed = {k: v for k, v in files.items() if k != "test_solution.py"}
    assert "hash" in g5_integrity.check(lock, removed).kinds()


def test_nc_g6_tampered_certificate_and_changed_code_rejected():
    cert = g6_certificate.issue(subject="x", requirements=[{"id": "R1", "text": "t"}], trace={"test_basic": ["R1"]},
                                admitted=["test_basic"], implementation=IMPL, tests=STRONG, gates={}, verdict="CERTIFY", residual={})
    assert g6_certificate.verify({**cert, "verdict": "REJECT"}, IMPL, STRONG) == (False, "bad signature")
    assert g6_certificate.verify(cert, IMPL_BUG, STRONG) == (False, "implementation changed since certification")
    assert g6_certificate.verify(cert, IMPL, STRONG + "\n") == (False, "tests changed since certification")
