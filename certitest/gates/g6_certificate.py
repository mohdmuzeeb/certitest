"""G6 Evidence certificate: a signed, machine-checkable verification record.

The certificate binds requirement -> tests -> gate verdicts to content hashes of
the tests, the implementation, and the toolchain. It is valid only for the
exact implementation hash it names; any code change invalidates it. Signing
uses HMAC-SHA256 with a local key (``.certitest_key``); a deployment would use
an asymmetric key held by the CI system.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import platform
import secrets
from datetime import datetime, timezone
from pathlib import Path

from .. import __version__
from .g5_integrity import sha256

KEY_PATH = Path(os.environ.get("CERTITEST_KEY", Path(__file__).resolve().parents[2] / ".certitest_key"))


def _key() -> bytes:
    if not KEY_PATH.exists():
        KEY_PATH.write_text(secrets.token_hex(32))
        KEY_PATH.chmod(0o600)
    return bytes.fromhex(KEY_PATH.read_text().strip())


def _canonical(d: dict) -> bytes:
    return json.dumps(d, sort_keys=True, separators=(",", ":")).encode()


def toolchain() -> dict:
    import pytest

    return {"python": platform.python_version(), "pytest": pytest.__version__, "certitest": __version__}


def issue(*, subject: str, requirements: list[dict], trace: dict[str, list[str]], admitted: list[str], implementation: str,
          tests: str, gates: dict, verdict: str, residual: dict) -> dict:
    per_req = {r["id"]: sorted(t for t, ids in trace.items() if r["id"] in ids and t in admitted) for r in requirements}
    body = {
        "schema": "certitest/certificate@1",
        "subject": subject,
        "issued_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "verdict": verdict,
        "implementation_sha256": sha256(implementation),
        "tests_sha256": sha256(tests),
        "toolchain": toolchain(),
        "requirements": per_req,
        "gates": gates,
        "residual_risk": residual,
    }
    body["signature"] = hmac.new(_key(), _canonical(body), hashlib.sha256).hexdigest()
    return body


def verify(cert: dict, implementation: str, tests: str) -> tuple[bool, str]:
    body = {k: v for k, v in cert.items() if k != "signature"}
    expected = hmac.new(_key(), _canonical(body), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, cert.get("signature", "")):
        return False, "bad signature"
    if cert["implementation_sha256"] != sha256(implementation):
        return False, "implementation changed since certification"
    if cert["tests_sha256"] != sha256(tests):
        return False, "tests changed since certification"
    return True, "valid"
