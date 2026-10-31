"""Tests for runtime_preflight scope-intake + session bootstrap."""
from __future__ import annotations

import subprocess, sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from scripts import runtime_preflight as pre


def _run(args):
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "runtime_preflight.py")] + args,
        cwd=str(ROOT), capture_output=True, text=True)


def test_normalize_strips_scheme_path_port_and_slash():
    assert pre.normalize_target("https://example.com/app?x=1") == "example.com"
    assert pre.normalize_target("example.com:81/") == "example.com:81"
    assert pre.normalize_target("  HTTP://x.Y.z  ") == "x.y.z"


@pytest.fixture
def isolated_ledger(tmp_path, monkeypatch):
    """Redirect the module's LEDGER_PATH to a temp copy so tests never touch the real ledger."""
    ledger = tmp_path / "CONTEXT_LEDGER.md"
    ledger.write_text(
        "# Context Ledger\n\n## Scope (authoritative)\n\n"
        "| Target (root domain) | In scope? | Boundaries / notes | Authorized by | Date |\n"
        "|----------------------|-----------|--------------------|---------------|------|\n",
        encoding="utf-8")
    monkeypatch.setattr(pre, "LEDGER_PATH", ledger)
    return ledger


def test_register_scope_adds_row(isolated_ledger):
    target = "pytest-intake-test.invalid"
    result = pre.register_scope(target, basis="owned", quiet=True)
    assert result["action"] == "added"
    assert result["target"] == target
    allowed, reason = pre.scope_status(target)
    assert allowed, reason
    # exactly one row for this target
    rows = [r for r in pre.load_scope_table() if r["target"] == target]
    assert len(rows) == 1


def test_register_scope_requires_basis():
    with pytest.raises(ValueError):
        pre.register_scope("nope.invalid", basis="i own it")


def test_register_scope_rejects_empty():
    with pytest.raises(ValueError):
        pre.register_scope("", basis="owned")


def test_register_scope_idempotent_updates(isolated_ledger):
    target = "pytest-idempotent-test.invalid"
    pre.register_scope(target, basis="owned", quiet=True)
    second = pre.register_scope(target, basis="owned", quiet=True)
    assert second["action"] == "updated"
    rows = [r for r in pre.load_scope_table() if r["target"] == target]
    assert len(rows) == 1


def test_register_scope_requires_basis_flag():
    proc = ROOT / "scripts" / "runtime_preflight.py"
    assert subprocess.run(
        [sys.executable, str(proc),
         "--register-scope", "--target", "nope.invalid", "--basis", "owned"],
        cwd=str(ROOT), capture_output=True, text=True).returncode == 0

    rc = subprocess.run(
        [sys.executable, str(proc),
         "--register-scope", "--target", "nope.invalid", "--basis", "hacked"],
        cwd=str(ROOT), capture_output=True, text=True)
    assert rc.returncode != 0


def test_register_scope_makes_target_in_scope(isolated_ledger):
    pre.register_scope("pytest-scope-test.invalid", basis="owned", quiet=True)
    allowed, reason = pre.scope_status("pytest-scope-test.invalid")
    assert allowed

