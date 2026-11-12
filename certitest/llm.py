"""LLM clients with a content-addressed disk cache.

Every completion is cached under a key derived from (provider, model, system,
messages, replicate). Re-running an experiment therefore replays identical
responses, which makes runs reproducible and avoids paying twice. The
``replicate`` index stands in for a sampling seed: the current Claude models
reject explicit sampling parameters, so independent samples are obtained by
issuing the same prompt under different replicate indices.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

CACHE_DIR = Path(os.environ.get("CERTITEST_CACHE", Path(__file__).resolve().parent.parent / ".cache" / "llm"))


@dataclass
class Completion:
    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    seconds: float = 0.0  # wall-clock of the original (uncached) call
    cached: bool = False


@dataclass
class Usage:
    calls: int = 0
    cached_calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    seconds: float = 0.0
    log: list = field(default_factory=list)

    def add(self, c: Completion, tag: str = "") -> None:
        self.calls += 1
        self.cached_calls += int(c.cached)
        self.input_tokens += c.input_tokens
        self.output_tokens += c.output_tokens
        self.seconds += c.seconds
        self.log.append({"tag": tag, "in": c.input_tokens, "out": c.output_tokens, "s": round(c.seconds, 2), "cached": c.cached})


class LLM:
    provider = "base"

    def __init__(self, model: str):
        self.model = model

    # -- subclasses implement _call ---------------------------------------
    def _call(self, system: str, messages: list[dict]) -> Completion:  # pragma: no cover
        raise NotImplementedError

    def complete(self, system: str, messages: list[dict], replicate: int = 0, usage: Usage | None = None, tag: str = "") -> Completion:
        key_src = json.dumps({"p": self.provider, "m": self.model, "s": system, "msg": messages, "r": replicate}, sort_keys=True)
        key = hashlib.sha256(key_src.encode()).hexdigest()
        path = CACHE_DIR / key[:2] / f"{key}.json"
        if path.exists():
            d = json.loads(path.read_text())
            c = Completion(d["text"], d["input_tokens"], d["output_tokens"], d["seconds"], cached=True)
        else:
            c = self._call(system, messages)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({"text": c.text, "input_tokens": c.input_tokens, "output_tokens": c.output_tokens,
                                        "seconds": c.seconds, "model": self.model, "provider": self.provider}))
        if usage is not None:
            usage.add(c, tag)
        return c

    @property
    def label(self) -> str:
        return f"{self.provider}:{self.model}"


class ClaudeLLM(LLM):
    """Anthropic Messages API via the official SDK. Credentials come from the environment."""

    provider = "anthropic"

    def __init__(self, model: str = "claude-opus-5-5", effort: str = "medium", max_tokens: int = 16000):
        super().__init__(model)
        import anthropic  # local import so the local-model path has no SDK dependency

        self._anthropic = anthropic
        self.client = anthropic.Anthropic(max_retries=4)
        self.effort = effort
        self.max_tokens = max_tokens

    def _call(self, system: str, messages: list[dict]) -> Completion:
        t0 = time.time()
        resp = self.client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system,
            messages=messages,
            thinking={"type": "adaptive"},
            output_config={"effort": self.effort},
        )
        if resp.stop_reason == "refusal":
            text = ""
        else:
            text = "".join(b.text for b in resp.content if b.type == "text")
        return Completion(text, resp.usage.input_tokens, resp.usage.output_tokens, time.time() - t0)


class OllamaLLM(LLM):
    """Local model served by Ollama (http://localhost:11434)."""

    provider = "ollama"

    def __init__(self, model: str = "qwen3:4b", host: str = "http://localhost:11434", num_ctx: int = 8192, num_predict: int = 3072):
        super().__init__(model)
        self.host = host
        self.num_ctx = num_ctx
        self.num_predict = num_predict

    def _call(self, system: str, messages: list[dict]) -> Completion:
        t0 = time.time()
        body = {
            "model": self.model,
            # qwen3 honours the /no_think switch; other models ignore it harmlessly
            "messages": [{"role": "system", "content": system + (" /no_think" if self.model.startswith("qwen3") else "")}] + messages,
            "stream": False,
            "think": False,
            "keep_alive": "30m",
            "options": {"num_ctx": self.num_ctx, "num_predict": self.num_predict, "temperature": 0.7},
        }
        req = urllib.request.Request(f"{self.host}/api/chat", data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=1200) as r:
            d = json.loads(r.read())
        text = re.sub(r"<think>.*?</think>", "", d["message"]["content"], flags=re.S)
        return Completion(text, d.get("prompt_eval_count", 0), d.get("eval_count", 0), time.time() - t0)


def make_llm(spec: str) -> LLM:
    """``anthropic:claude-opus-5-5`` or ``ollama:qwen3:4b``."""
    provider, _, model = spec.partition(":")
    if provider == "anthropic":
        return ClaudeLLM(model or "claude-opus-5-5")
    if provider == "ollama":
        return OllamaLLM(model or "qwen3:4b")
    raise ValueError(f"unknown provider in {spec!r}")


def extract_code(text: str) -> str:
    """Return the last fenced Python block, or the raw text if none."""
    import re

    blocks = re.findall(r"```(?:python|py)?\s*\n(.*?)```", text, flags=re.S)
    return (blocks[-1] if blocks else text).strip() + "\n"
