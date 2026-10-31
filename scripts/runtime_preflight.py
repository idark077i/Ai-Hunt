#!/usr/bin/env python3
"""runtime_preflight.py — boot gate + session bootstrap creation.

Checks (fail = non-zero exit):
  1. required files and directories exist
  2. runtime/registry.json parses and passes skill_registry.check()
  3. learning_engine imports and SQLite DB initializes
  4. memory corpus files exist
  5. agent/command files referenced by the registry exist

Creates:
  - logs/sessions/<timestamp>.json  (session bootstrap block)
  - learning_engine/memory/learning.db (via engine init, if missing)

Usage:
  python scripts/runtime_preflight.py            # gate check only
  python scripts/runtime_preflight.py --bootstrap  # gate + create session block
  python scripts/runtime_preflight.py --quiet      # suppress per-check output
  python scripts/runtime_preflight.py --target "example.com" --scenario "login form" \
      --mode hunt --workspace "testing/example.com/www" --ensure-session
      # gate + scope intake + session bootstrap note

Scope lock: a target is testable ONLY when it appears as `in` in the
pipe-delimited scope table of memory/CONTEXT_LEDGER.md. Anything else (missing,
`out`, workspace/target mismatch) blocks with a non-zero exit and a clear
message. Ambiguity = out of scope.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

REQUIRED_FILES = [
    "AGENTS.md",
    "prioritization.md",
    "agent-briefing.md",
    "opencode.json",
    ".opencode.json",
    "package.json",
    "runtime/registry.json",
    "runtime/registry-claude-red.json",
    "runtime/opencode/loader.md",
    "runtime/opencode/ORCHESTRATION.md",
    "runtime/opencode/manifest.md",
    "runtime/antigravity/loader.md",
    "runtime/antigravity/manifest.md",
    "scripts/skill_registry.py",
    "scripts/runtime_preflight.py",
    "scripts/sync_opencode_agents.py",
    "scripts/test_skill_registry.py",
    "scripts/test_runtime_contract.py",
    "memory/report-lessons.md",
    "memory/patterns.md",
    "memory/methodology.md",
    "memory/FALSE_POSITIVE_DB.md",
    "memory/CONTEXT_LEDGER.md",
]

REQUIRED_DIRS = [
    "skills",
    ".agents/skills",
    "scripts",
    "learning_engine",
    "memory/targets",
    "writeups/raw",
    "writeups/processed",
    "writeups/metadata",
    "writeups/feeds",
    ".opencode/commands",
    ".opencode/agents",
    "testing",
    "wordlist",
    "logs/sessions",
    "logs/runs",
]

COMMANDS = [
    "hunt", "scan", "recon", "dorking", "understand", "update", "report", "mindmap",
]


def report(msg: str, quiet: bool) -> None:
    if not quiet:
        print(msg)


def run_gate(quiet: bool = False) -> list[str]:
    problems: list[str] = []

    for rel in REQUIRED_FILES:
        if not (ROOT / rel).exists():
            problems.append(f"missing file: {rel}")
    for rel in REQUIRED_DIRS:
        if not (ROOT / rel).is_dir():
            problems.append(f"missing dir: {rel}")

    for cmd in COMMANDS:
        if not (ROOT / ".opencode" / "commands" / f"{cmd}.md").exists():
            problems.append(f"missing command: .opencode/commands/{cmd}.md")

    # Agents must be materialised into the OpenCode V2 discovery path. Without
    # this, `/hunt` names sub-agents that do not exist and orchestration
    # silently no-ops.
    try:
        from scripts.sync_opencode_agents import check_agents  # noqa: PLC0415
        problems += check_agents()
    except Exception as exc:  # noqa: BLE001
        problems.append(f"agent sync check failed: {exc}")
    n_agents = len(list((ROOT / ".opencode" / "agents").glob("*.md")))
    report(f"[ok] opencode agents: {n_agents} materialised", quiet)

    from scripts.skill_registry import check, _load_registry, _load_catalog  # noqa: PLC0415
    try:
        registry = _load_registry()
        catalog = _load_catalog()
        problems += [f"registry: {p}" for p in check(registry, catalog)]
    except SystemExit as exc:
        problems.append(f"registry load failed: {exc}")

    try:
        from learning_engine import engine  # noqa: PLC0415, F401
        eng = engine.LearningEngine(root=ROOT)
        eng.init_db()
        report(f"[ok] learning engine db: {eng.db_path}", quiet)
    except Exception as exc:  # noqa: BLE001
        problems.append(f"learning engine failed to initialize: {exc}")

    report(f"[ok] required files: {len(REQUIRED_FILES)} checked", quiet)
    report(f"[ok] required dirs: {len(REQUIRED_DIRS)} checked", quiet)
    report(f"[ok] commands: {len(COMMANDS)} checked", quiet)
    return problems


def bootstrap(quiet: bool = False) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    block = {
        "session": stamp,
        "kind": "bootstrap",
        "agent": "runtime_preflight",
        "scope": "read-only",
        "next": [
            "read agent-briefing.md",
            "resolve scenarios: python scripts/skill_registry.py resolve <scenario>",
            "start hunts via .opencode/commands/hunt.md",
        ],
    }
    dst = ROOT / "logs" / "sessions" / f"{stamp}.json"
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(json.dumps(block, indent=2), encoding="utf-8")
    report(f"[ok] session bootstrap: {dst}", quiet)
    return dst


# ---------------------------------------------------------------------------
# Scope intake (session bootstrap)
# ---------------------------------------------------------------------------

LEDGER_PATH = ROOT / "memory" / "CONTEXT_LEDGER.md"

# Columns of the pipe-delimited scope table in memory/CONTEXT_LEDGER.md.
# | Target (root domain) | In scope? | Boundaries / notes | Authorized by | Date |
SCOPE_COLUMNS = ("target", "in_scope", "boundaries", "authorized_by", "date")

IN_SCOPE_MARKERS = ("in", "yes", "true", "1")

# Standard target workspace layout created deterministically at session start.
WORKSPACE_FOLDERS = (
    "notes",
    "requests",
    "responses",
    "screenshots",
    "scripts",
    "scans",
    "wordlists",
    "urls",
    "subdomains",
    "evidence",
    "reports",
)


def load_scope_table() -> list[dict]:
    """Parse the pipe-delimited scope table from CONTEXT_LEDGER.md."""
    if not LEDGER_PATH.exists():
        return []
    rows: list[dict] = []
    for line in LEDGER_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 2 or not any(cells):
            continue
        target = cells[0].strip("`")
        if not target or target == "Target (root domain)" or target.startswith("-"):
            continue
        rows.append({
            "target": target,
            "in_scope": cells[1] if len(cells) > 1 else "out",
            "boundaries": cells[2] if len(cells) > 2 else "",
            "authorized_by": cells[3] if len(cells) > 3 else "",
            "date": cells[4] if len(cells) > 4 else "",
        })
    return rows


def scope_status(target: str) -> tuple[bool, str]:
    """Return (allowed, reason). Exact match on the target root wins."""
    target = target.strip().lower()
    for row in load_scope_table():
        if row["target"].lower() == target:
            allowed = row["in_scope"].lower() in IN_SCOPE_MARKERS
            reason = (f"ledger entry '{target}': in-scope={row['in_scope']}"
                      if allowed else
                      f"ledger entry '{target}' is marked '{row['in_scope']}' - OUT OF SCOPE")
            return allowed, reason
    return False, f"target '{target}' has NO ledger entry in {LEDGER_PATH.relative_to(ROOT)} - ambiguity = OUT OF SCOPE"


# Authorization bases the intake accepts. The user states the basis in one line;
# it is recorded verbatim (audit trail) — never inferred.
AUTH_BASES = {
    "owned": "I own this target",
    "bugbounty": "bug bounty program (paid, in-scope)",
    "pentest": "authorized penetration test (contract/scope doc)",
    "sandbox": "local/synthetic sandbox (no production traffic)",
}

SCOPE_TABLE_HEADER = "| Target (root domain) | In scope? | Boundaries / notes | Authorized by | Date |"


def normalize_target(raw: str) -> str:
    """Reduce a user-supplied target to its canonical ledger key.

    Strips scheme, path, port, and trailing slash. Keeps the host as given so
    an exact-host scope stays exact-host.
    """
    t = raw.strip().strip("`").strip()
    for scheme in ("https://", "http://"):
        if t.lower().startswith(scheme):
            t = t[len(scheme):]
    t = t.split("/", 1)[0].split("?", 1)[0]
    return t.strip().lower()


def infer_boundaries(target: str, basis: str) -> str:
    """Derive a conservative, explicit boundary note for the ledger row."""
    host = normalize_target(target)
    notes = [f"authorized via: {AUTH_BASES.get(basis, basis)}"]
    if host.startswith("*."):
        notes.append("wildcard scope limited to that suffix")
    else:
        notes.append(f"exact-host scope = {host} only (no sibling hosts, no FUZZ.<root>)")
    notes.append("no OOB (no Collaborator domain unless supplied)")
    notes.append("no DoS/destructive writes; no real-user data")
    return "; ".join(notes)


def register_scope(
    target: str,
    basis: str,
    boundaries: str | None = None,
    quiet: bool = False,
) -> dict:
    """Register a target in the scope table from a one-line authorization basis.

    Writes (or updates) the ledger row so `ensure_session`/`scope_status` accept
    it. Idempotent: re-registering the same target updates its row in place.

    Raises ValueError when the basis is missing/unknown or the target is empty.
    """
    host = normalize_target(target)
    if not host:
        raise ValueError("scope intake: empty target")
    if basis not in AUTH_BASES:
        raise ValueError(
            f"scope intake: unknown authorization basis '{basis}' "
            f"(known: {', '.join(sorted(AUTH_BASES))})")

    note = boundaries.strip() if boundaries else infer_boundaries(host, basis)
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    row = f"| {host} | in | {note} | master | {date} |"

    lines = LEDGER_PATH.read_text(encoding="utf-8").splitlines()
    # Find the scope table and replace/mint the row without disturbing anything.
    header_idx = next((i for i, ln in enumerate(lines)
                       if ln.strip() == SCOPE_TABLE_HEADER), None)
    if header_idx is None:
        raise ValueError(f"scope intake: scope table header not found in "
                         f"{LEDGER_PATH.relative_to(ROOT)}")

    # The table body runs from header+2 (skip separator) until the first blank
    # line or the next heading.
    body_start = header_idx + 2
    body_end = len(lines)
    for i in range(body_start, len(lines)):
        stripped = lines[i].strip()
        if not stripped or not stripped.startswith("|"):
            body_end = i
            break

    existing = None
    for i in range(body_start, body_end):
        cells = [c.strip() for c in lines[i].strip().strip("|").split("|")]
        if cells and normalize_target(cells[0]) == host:
            existing = i
            break

    if existing is not None:
        lines[existing] = row
        action = "updated"
    else:
        lines.insert(body_end, row)
        action = "added"

    LEDGER_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    report(f"[ok] scope {action}: {row}", quiet)
    return {"action": action, "target": host, "row": row, "basis": basis}


def ensure_session(target: str, scenario: str, mode: str, workspace: str | None, quiet: bool = False) -> Path:
    """Scope intake + session bootstrap. Raises ValueError on any block."""
    allowed, reason = scope_status(target)
    if not allowed:
        raise ValueError(f"scope blocked: {reason}")

    if workspace:
        ws = Path(workspace)
        if not str(ws).replace("\\", "/").startswith("testing/"):
            raise ValueError(f"workspace must live under testing/, got: {workspace}")
        if target.lower() not in str(ws).lower():
            raise ValueError(
                f"workspace '{workspace}' does not match target '{target}' - refusing out-of-scope workspace")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    block = {
        "session": stamp,
        "kind": "session-bootstrap",
        "agent": "runtime_preflight",
        "target": target,
        "scenario": scenario,
        "mode": mode,
        "workspace": workspace,
        "scope": "locked",
        "next": [
            "read agent-briefing.md",
            f"resolve: python scripts/skill_registry.py resolve --scenario \"{scenario}\" --mode {mode} --paths-only",
            "start exploitation first; collection agents are fire-and-forget",
        ],
    }
    dst = ROOT / "logs" / "sessions" / f"{stamp}.json"
    dst.parent.mkdir(parents=True, exist_ok=True)
    dst.write_text(json.dumps(block, indent=2), encoding="utf-8")
    report(f"[ok] session bootstrap: {dst}", quiet)

    if workspace:
        ws_root = ROOT / workspace
        for folder in WORKSPACE_FOLDERS:
            (ws_root / folder).mkdir(parents=True, exist_ok=True)
        notes = ws_root / "notes"
        note = notes / "bootstrap.md"
        note.write_text(
            "# Session Bootstrap\n\n"
            f"- Target: `{target}`\n"
            f"- Scenario: `{scenario}`\n"
            f"- Mode: `{mode}`\n"
            f"- Bootstrap: `{dst.relative_to(ROOT)}`\n"
            f"- Started: {datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}\n"
            f"- Scope: locked (per {LEDGER_PATH.relative_to(ROOT)})\n",
            encoding="utf-8",
        )
        report(f"[ok] workspace bootstrap note: {note}", quiet)
    return dst


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Boot gate + session bootstrap")
    parser.add_argument("--bootstrap", action="store_true", help="also create a session block")
    parser.add_argument("--quiet", action="store_true", help="suppress per-check output")
    parser.add_argument("--target", help="exact target for scope intake (required with --ensure-session)")
    parser.add_argument("--scenario", default="", help="initial surface/scenario")
    parser.add_argument("--mode", choices=["hunt", "recon", "understand", "scan",
                                           "update", "report"], default="hunt")
    parser.add_argument("--workspace", help="testing/<root>/<sub> workspace for this session")
    parser.add_argument("--ensure-session", action="store_true",
                        help="scope intake + session bootstrap note (blocks on out-of-scope)")
    parser.add_argument("--register-scope", action="store_true",
                        help="register --target in the scope table from --basis "
                             "(one-line authorization), then continue")
    parser.add_argument("--basis", choices=sorted(AUTH_BASES),
                        help="authorization basis for --register-scope")
    parser.add_argument("--boundaries",
                        help="override the inferred boundary note for --register-scope "
                             "(quoted string)")
    args = parser.parse_args(argv)

    problems = run_gate(quiet=args.quiet)
    for p in problems:
        print(f"[FAIL] {p}", file=sys.stderr)

    if problems:
        print(f"preflight FAILED with {len(problems)} problem(s) - do not proceed", file=sys.stderr)
        return 1

    if args.register_scope:
        if not args.target:
            print("[FAIL] --register-scope requires --target <exact target>", file=sys.stderr)
            return 1
        if not args.basis:
            print("[FAIL] --register-scope requires --basis "
                  f"({'|'.join(sorted(AUTH_BASES))}) - the authorization basis "
                  "is never inferred", file=sys.stderr)
            return 1
        try:
            register_scope(target=args.target, basis=args.basis,
                           boundaries=args.boundaries, quiet=args.quiet)
        except ValueError as exc:
            print(f"[FAIL] {exc}", file=sys.stderr)
            return 1

    if args.ensure_session:
        if not args.target:
            if args.mode != "update":
                print("[FAIL] --ensure-session requires --target <exact target> "
                      "(unless --mode update)", file=sys.stderr)
                return 1
            dst = bootstrap(quiet=args.quiet)
            print("session bootstrap OK - workspace-level update session, READY")
            return 0
        try:
            ensure_session(
                target=args.target,
                scenario=args.scenario or "initial recon",
                mode=args.mode,
                workspace=args.workspace,
                quiet=args.quiet,
            )
        except ValueError as exc:
            print(f"[FAIL] {exc}", file=sys.stderr)
            print("session bootstrap BLOCKED - do not proceed", file=sys.stderr)
            return 1
        print("session bootstrap OK - scope locked, READY TO HUNT")
        return 0

    print("preflight OK - workspace is ready")
    if args.bootstrap:
        bootstrap(quiet=args.quiet)
    return 0


if __name__ == "__main__":
    sys.exit(main())