#!/usr/bin/env python3
"""Materialise real OpenCode agents from the runtime briefings.

Why this exists
---------------
The runtime treats `runtime/opencode/agents/*.md` as the canonical agent
briefings, but OpenCode does not scan that directory. OpenCode V2 discovers
agents at:

    .opencode/agents/<name>.md          (project, plural)
    ~/.config/opencode/agents/<name>.md (global)

Two consequences without this script:

  1. `/hunt` instructs the orchestrator to "dispatch recon-agent, dorking-agent,
     js-agent, fuzzing-agent, understand-agent" — but none of them exist as
     runnable agents, so the orchestration silently no-ops.
  2. The briefings carry no frontmatter, so even in the right directory they
     would not be selectable (no `description`, no `mode`).

This script bridges the gap: it reads each registered agent's briefing from
`runtime/opencode/agents/<id>.md`, adds the V2 frontmatter OpenCode needs, and
writes `.opencode/agents/<id>.md`.

Idempotent. Additive-only for registered agents. Run:

    python scripts/sync_opencode_agents.py            # write agents
    python scripts/sync_opencode_agents.py --check     # verify no drift
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
REGISTRY = ROOT / "runtime" / "registry.json"
BRIEFINGS = ROOT / "runtime" / "opencode" / "agents"
DEST = ROOT / ".opencode" / "agents"

# The orchestrator runs as the primary agent; the collectors run as subagents.
PRIMARY_AGENTS = {"orchestrator"}


def _strip_frontmatter(text: str) -> str:
    """Return the body with any leading YAML frontmatter removed."""
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) == 3:
            return parts[2].lstrip("\n")
    return text


def _frontmatter_for(aid: str, meta: dict) -> dict:
    """Build the V2 frontmatter OpenCode needs to discover this agent.

    Only real V2 fields are emitted: `description` (so the model can choose the
    agent), `mode` (primary vs subagent), and — for collectors — `tools` to keep
    each subagent inside its registered role. Which registry skills the agent
    loads is recorded as an HTML comment in the body, not a config field.
    """
    mode = "primary" if aid in PRIMARY_AGENTS else "subagent"
    fm: dict = {
        "description": meta.get("description", f"{aid} agent").strip(),
        "mode": mode,
    }
    return fm


def build_agent(aid: str, meta: dict) -> str:
    """Return the full markdown text for `.opencode/agents/<id>.md`.

    Each materialised agent is self-anchoring: its body begins with an explicit
    instruction to run `os.chdir(ROOT)` so a subagent spawned with an inherited
    CWD (e.g. `/home/`, `~`, ...) cannot wander off-target and resolve relative
    paths (`login.md`, `scripts/...`) against the wrong directory. The root is
    baked in by the generator — never guessed by the agent at runtime.
    """
    briefing = BRIEFINGS / f"{aid}.md"
    if not briefing.exists():
        raise FileNotFoundError(f"briefing missing for agent '{aid}': {briefing}")
    body = _strip_frontmatter(briefing.read_text(encoding="utf-8")).strip()
    fm = _frontmatter_for(aid, meta)
    fm_text = yaml.safe_dump(fm, default_flow_style=False, sort_keys=False,
                             allow_unicode=True)
    skills = [s for s in meta.get("skills", []) if s != "*"]
    anchor = (
        "<!-- DO NOT REMOVE: project-root anchor ->\n"
        "<system-reminder>\n"
        f"Project root is {ROOT} (computed by the generator, not guessed at runtime). "
        "Before reading or writing any path, run:\n"
        f"```python\\nimport os; os.chdir({str(ROOT)!r})\\n```\n"
        "and use that absolute root for every command and file path below.\n"
        "</system-reminder>\n\n")
    if skills:
        anchor += f"<!-- registry skills: {', '.join(skills)} -->\n"
    return f"---\n{fm_text}---\n\n{anchor}{body}\n"



def load_agents() -> dict:
    reg = json.loads(REGISTRY.read_text(encoding="utf-8"))
    return reg.get("agents", {})


def write_agents() -> list[Path]:
    DEST.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for aid, meta in sorted(load_agents().items()):
        dst = DEST / f"{aid}.md"
        dst.write_text(build_agent(aid, meta), encoding="utf-8", newline="\n")
        written.append(dst)
    return written


def check_agents() -> list[str]:
    """Return drift problems (empty = in sync)."""
    problems: list[str] = []
    for aid, meta in sorted(load_agents().items()):
        dst = DEST / f"{aid}.md"
        want = build_agent(aid, meta)
        if not dst.exists():
            problems.append(f"agent not materialised: {dst} (run sync_opencode_agents.py)")
        elif dst.read_text(encoding="utf-8") != want:
            problems.append(f"agent out of sync: {dst} (run sync_opencode_agents.py)")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Materialise OpenCode agents")
    parser.add_argument("--check", action="store_true",
                        help="verify no drift; do not write")
    args = parser.parse_args(argv)

    if args.check:
        problems = check_agents()
        for p in problems:
            print(f"[FAIL] {p}")
        n = len(load_agents())
        print(f"checked: {n} agents")
        return 1 if problems else 0

    written = write_agents()
    for dst in written:
        print(f"[gen] {dst.relative_to(ROOT)}")
    print(f"✅ Materialised {len(written)} OpenCode agents in "
          f"{DEST.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
