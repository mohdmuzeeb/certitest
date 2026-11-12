"""Hermetic-ish execution of pytest suites in throwaway directories.

Each run copies the workspace files into a fresh temp directory and executes
pytest in a subprocess with a scrubbed environment (fixed hash seed, no proxy
or credential variables, isolated mode). Per-test outcomes are read from the
JUnit XML report. This is process isolation, not a security sandbox; network
access is not blocked (see Threats to Validity).
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

PY = sys.executable
_SAFE_ENV_KEYS = ("PATH", "HOME", "LANG", "LC_ALL", "TMPDIR")


@dataclass
class RunResult:
    outcomes: dict[str, str] = field(default_factory=dict)  # test name -> passed|failed|error|skipped
    collected: bool = True
    timed_out: bool = False
    stderr: str = ""
    stdout: str = ""

    @property
    def failing(self) -> list[str]:
        return [t for t, o in self.outcomes.items() if o in ("failed", "error")]

    @property
    def passing(self) -> list[str]:
        return [t for t, o in self.outcomes.items() if o == "passed"]


def _env() -> dict:
    env = {k: os.environ[k] for k in _SAFE_ENV_KEYS if k in os.environ}
    env.update({"PYTHONHASHSEED": "0", "PYTHONDONTWRITEBYTECODE": "1", "TZ": "UTC"})
    return env


def run_pytest(files: dict[str, str], test_file: str = "test_generated.py", timeout: float = 30.0, select: list[str] | None = None) -> RunResult:
    with tempfile.TemporaryDirectory(prefix="certitest_") as d:
        root = Path(d)
        for name, content in files.items():
            p = root / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content)
        xml = root / "_report.xml"
        args = [PY, "-m", "pytest", test_file, "-q", "-p", "no:cacheprovider", "-p", "no:randomly",
                f"--junitxml={xml}", "-o", "junit_family=xunit1", "--rootdir", str(root)]
        if select:
            args += ["-k", " or ".join(select)]
        try:
            proc = subprocess.run(args, cwd=root, env=_env(), capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired as e:
            return RunResult(collected=True, timed_out=True, stderr=str(e)[:2000])
        res = RunResult(stdout=proc.stdout[-4000:], stderr=proc.stderr[-2000:])
        if not xml.exists():
            res.collected = False
            return res
        tree = ET.parse(xml)
        for tc in tree.iter("testcase"):
            name = tc.get("name", "")
            if not name:
                continue
            outcome = "passed"
            for child in tc:
                if child.tag == "failure":
                    outcome = "failed"
                elif child.tag == "error":
                    outcome = "error"
                elif child.tag == "skipped":
                    outcome = "skipped"
            res.outcomes[name] = outcome
        # pytest exit code 2 = interrupted/collection error, 4 = usage error, 5 = no tests
        if proc.returncode in (2, 3, 4) and not res.outcomes:
            res.collected = False
        return res


def run_many(jobs: list[dict[str, str]], test_file: str = "test_generated.py", timeout: float = 30.0, workers: int = 8) -> list[RunResult]:
    with ThreadPoolExecutor(max_workers=workers) as ex:
        return list(ex.map(lambda f: run_pytest(f, test_file, timeout), jobs))


# ---------------------------------------------------------------- batched mutant execution
_BATCH_RUNNER = r'''
import json, os, sys, importlib
import xml.etree.ElementTree as ET
import pytest
spec = json.load(open(sys.argv[1]))
root = os.getcwd()
sys.path.insert(0, root)
results = []
for i, src in enumerate(spec["mutants"]):
    with open(os.path.join(root, spec["module"] + ".py"), "w") as f:
        f.write(src)
    for m in [m for m in list(sys.modules) if m in (spec["module"], "test_generated", "conftest") or m.startswith(spec["module"] + ".")]:
        del sys.modules[m]
    importlib.invalidate_caches()
    xml = os.path.join(root, f"_r{i}.xml")
    pid = os.fork()
    if pid == 0:
        os.close(1); os.close(2)
        os.open(os.devnull, os.O_WRONLY); os.open(os.devnull, os.O_WRONLY)
        code = pytest.main(["test_generated.py", "-q", "-p", "no:cacheprovider", f"--junitxml={xml}", "-o", "junit_family=xunit1"])
        os._exit(int(code) if isinstance(code, int) else 1)
    import signal, time
    deadline = time.time() + spec["timeout"]
    status = None
    while time.time() < deadline:
        done, st = os.waitpid(pid, os.WNOHANG)
        if done:
            status = st
            break
        time.sleep(0.02)
    if status is None:
        os.kill(pid, signal.SIGKILL); os.waitpid(pid, 0)
        results.append({"timed_out": True, "collected": True, "outcomes": {}})
        continue
    out = {}
    if os.path.exists(xml):
        for tc in ET.parse(xml).iter("testcase"):
            o = "passed"
            for ch in tc:
                o = {"failure": "failed", "error": "error", "skipped": "skipped"}.get(ch.tag, o)
            out[tc.get("name", "")] = o
    results.append({"timed_out": False, "collected": os.path.exists(xml) and bool(out), "outcomes": out})
print(json.dumps(results))
'''


def run_mutants_batched(files: dict[str, str], module: str, mutant_sources: list[str], timeout_each: float = 20.0) -> list[RunResult]:
    """Run the same test file against many mutants: pytest is imported once and forked per mutant."""
    import json as _json

    with tempfile.TemporaryDirectory(prefix="certitest_mut_") as d:
        root = Path(d)
        for name, content in files.items():
            (root / name).write_text(content)
        (root / "_spec.json").write_text(_json.dumps({"module": module, "mutants": mutant_sources, "timeout": timeout_each}))
        (root / "_batch.py").write_text(_BATCH_RUNNER)
        try:
            proc = subprocess.run([PY, "_batch.py", "_spec.json"], cwd=root, env=_env(), capture_output=True, text=True,
                                  timeout=timeout_each * len(mutant_sources) + 60)
            data = _json.loads(proc.stdout.strip().splitlines()[-1])
        except Exception as e:  # fall back to one subprocess per mutant
            jobs = [{**files, f"{module}.py": s} for s in mutant_sources]
            return run_many(jobs, timeout=timeout_each)
    return [RunResult(outcomes=r["outcomes"], collected=r["collected"], timed_out=r["timed_out"]) for r in data]
