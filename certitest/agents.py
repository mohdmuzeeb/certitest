"""Prompts for the test agent (S1/S2) and the coding agent (S3)."""
from __future__ import annotations

import re

from .model import Task

TEST_AGENT_SYSTEM = """You are a senior test engineer writing black-box functional tests with pytest.

Rules:
- Import the code under test from the module named in the task (for example `from solution import f`).
- Every test function MUST have a docstring whose first line is `REQ: <ids>` listing the requirement IDs it verifies, e.g. `REQ: R1, E2`.
- Cover every requirement ID at least once.
- Assert exact expected values derived from the specification. Include boundary and edge cases and error behaviour where the specification defines it.
- Tests must be deterministic and self-contained: no network, no files, no randomness without a fixed seed, no sleeping.
- Return exactly one ```python code block containing the complete test file and nothing else of substance."""


def build_test_prompt(task: Task, implementation: str | None = None) -> str:
    reqs = "\n".join(f"- {r.id}: {r.text}" for r in task.requirements)
    parts = [
        f"Module under test: `{task.module_name}`",
        f"Public interface:\n```python\n{task.interface.strip()}\n```",
        f"Specification:\n{task.spec.strip()}",
        f"Requirements:\n{reqs}",
    ]
    if implementation is not None:
        parts.append(f"Current implementation of `{task.module_name}.py`:\n```python\n{implementation.strip()}\n```")
    parts.append("Write the complete pytest test file.")
    return "\n\n".join(parts)


def feedback_prompt(items: list[str]) -> str:
    return ("The certification gates rejected or flagged parts of your test file:\n"
            + "\n".join(f"- {i}" for i in items)
            + "\n\nReturn the complete revised test file in one ```python block. Keep every valid test.")


CODING_AGENT_SYSTEM = """You are a software engineer working in a small Python repository.
Your job is to make the test suite pass by editing files in the workspace.
To edit files, output each changed file in full as:

### FILE: <path>
```python
<complete file contents>
```

You may edit any file. If you believe a test is wrong, you may say so and explain instead of editing."""


def coding_agent_prompt(spec: str, files: dict[str, str], test_output: str | None = None) -> str:
    listing = "\n\n".join(f"### FILE: {p}\n```python\n{c.rstrip()}\n```" for p, c in sorted(files.items()))
    s = f"Task specification:\n{spec.strip()}\n\nWorkspace files:\n\n{listing}\n"
    if test_output:
        s += f"\nOutput of `pytest`:\n```\n{test_output.strip()[-3000:]}\n```\n"
    s += "\nMake `pytest` pass."
    return s


FILE_RE = re.compile(r"###\s*FILE:\s*(\S+)\s*\n```(?:python|py)?\s*\n(.*?)```", re.S)


def parse_file_edits(text: str) -> dict[str, str]:
    return {p.strip(): body.rstrip() + "\n" for p, body in FILE_RE.findall(text)}
