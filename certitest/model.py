"""Core data model shared by subjects and the pipeline."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Requirement:
    id: str
    text: str


@dataclass
class Task:
    subject: str               # s1 | s2 | s3
    task_id: str
    module_name: str           # module the tests import, e.g. "solution"
    entry_point: str           # primary callable/class under test
    spec: str                  # natural-language specification shown to the test agent
    interface: str             # public interface (signatures only)
    requirements: list[Requirement] = field(default_factory=list)
    extra: dict = field(default_factory=dict)

    @property
    def requirement_ids(self) -> list[str]:
        return [r.id for r in self.requirements]


@dataclass
class Candidate:
    cand_id: str
    task_id: str
    source: str                # full module source
    label: str                 # correct | faulty   (ground truth; never shown to agents or gates)
    origin: str                # canonical | llm-correct | llm-faulty | heldout-mutant | seeded
    detail: dict = field(default_factory=dict)
