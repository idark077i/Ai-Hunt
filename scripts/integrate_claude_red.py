#!/usr/bin/env python3
"""OpenCode-only integration of Claude-Red skills.

Reads the pristine Claude-Red library at Claude-Red/Skills/<category>/<folder>/SKILL.md
(a YAML-frontmatter file) and wires every skill into the OpenCode runtime:

  1. Generates a normalised flat copy in skills/claude-red/<category>-<folder>.md
     (frontmatter name/description preserved, body kept intact).
  2. Generates a runtime copy in .agents/skills/<name>/SKILL.md.
   3. Writes a separate runtime/registry-claude-red.json catalog so the
      curated core registry remains small and deterministic.
  4. Runs skill_registry.py check to validate.

Idempotent.  Does NOT touch .pentestcode/.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
SRC_TREE = ROOT / "Claude-Red" / "Skills"        # pristine category/folders
FLAT_DIR = ROOT / "skills" / "claude-red"         # normalised flat output
RUNTIME = ROOT / ".agents" / "skills"
REG = ROOT / "runtime" / "registry.json"
CLAUDE_RED_REG = ROOT / "runtime" / "registry-claude-red.json"
PROVENANCE = ROOT / "runtime" / "claude-red.provenance.json"

ID_TO_CATEGORY = {
    "ad": "active-directory", "ai": "ai", "api": "api", "auth": "auth",
    "cicd": "cicd", "cloud": "cloud", "container": "container",
    "crypto": "crypto", "exploit-dev": "exploit-dev", "fuzzing": "fuzzing",
    "infrastructure": "infrastructure", "iot": "iot", "mobile": "mobile",
    "network": "network", "post-exploitation": "post-exploitation",
    "privesc": "privesc", "recon": "recon",
    "social-engineering": "social-engineering", "supply-chain": "supply-chain",
    "utility": "utility", "web": "web", "wireless": "wireless",
    "forensics": "utility",
}

PHASE_BY_CATEGORY = {"recon": "recon"}


def parse_frontmatter(text: str) -> tuple[dict | None, str]:
    """Parse leading YAML frontmatter. Returns (fm_dict, body) or (None, text)."""
    m = re.match(r"\A---\s*\n(.*?)\n---\s*\n(.*)\Z", text, re.S)
    if not m:
        return None, text
    raw = m.group(1)
    # Use safe_load to handle block scalars (description: >) correctly
    data = yaml.safe_load(raw)
    if data is None:
        data = {}
    return (data if isinstance(data, dict) else {}), m.group(2)


# Generic tokens that collide with core OpenCode skill aliases — offensive
# skills must NOT claim these (first-wins policy reserves them for core skills).
_GENERIC = {
    "sqli", "ssrf", "xss", "ssti", "idor", "csrf", "lfi", "xxe", "rce",
    "jwt", "oauth", "graphql", "recon", "osint", "dorking", "fuzzing",
    "cloud", "mobile", "api", "auth", "ai", "llm", "kubernetes", "k8s",
    "docker", "container", "tls", "vpn", "wifi", "iot", "web", "network",
    "crypto", "saml", "openid", "openid-connect",
}


def parse_legacy(text: str) -> tuple[dict, str, list[str]]:
    """Extract (fm_dict, body, triggers) from legacy ## Metadata block format."""
    name = ""
    desc = ""
    triggers: list[str] = []
    folder = ""
    source = ""

    fm = re.search(r"\*\*Folder\*\*\s*:\s*(.+)", text)
    if fm:
        folder = fm.group(1).strip()
        name = folder
    sm = re.search(r"\*\*Skill Name\*\*\s*:\s*(.+)", text)
    if sm and not name:
        name = sm.group(1).strip()
    sv = re.search(r"\*\*Source\*\*\s*:\s*(.+)", text)
    if sv:
        source = sv.group(1).strip()

    # Description: collect lines between "## Description" and next "##" header
    dm = re.search(r"## Description\s*\n(.+?)(?:\n##|\Z)", text, re.S)
    if dm:
        desc = " ".join(dm.group(1).split())

    # Trigger phrases: backtick-comma lists under ## Trigger Phrases
    tp = re.search(r"## Trigger Phrases\s*\n(.+?)(?:\n##|\Z)", text, re.S)
    if tp:
        for bt in re.finditer(r"`([^`]+)`", tp.group(1)):
            for tok in bt.group(1).split(","):
                tok = tok.strip().lower()
                if tok and len(tok) > 1 and tok not in _GENERIC and tok not in triggers:
                    triggers.append(tok)

    # Body = the actionable content AFTER the metadata/description/trigger
    # header block. Those three sections are redundant once captured in
    # frontmatter, so we start the body at the first real content section,
    # preserving the complete "Instructions for Claude" + "Full Methodology"
    # (the actual payload material). Never keep only the leading title.
    body = text
    start = -1
    for marker in ("## Instructions for Claude", "## Full Methodology"):
        idx = body.find(marker)
        if idx != -1 and (start == -1 or idx < start):
            start = idx
    if start != -1:
        body = body[start:]
    else:
        # Fallback: drop each known header section individually, keeping the rest.
        body = re.sub(
            r"## Metadata.*?(?=\n## (?:Instructions|Full)|\Z)", "", body, flags=re.S
        )
        body = re.sub(r"## Description.*?(?=\n## |\Z)", "", body, flags=re.S)
        body = re.sub(r"## Trigger Phrases.*?(?=\n## |\Z)", "", body, flags=re.S)
    body = body.strip() or text.strip()

    fm = {"name": name, "description": desc, "source": source}
    return fm, body, triggers


def parse_any(text: str) -> tuple[dict, str, list[str]]:
    """Unified parser: try YAML frontmatter first, then legacy ## Metadata.

    Returns (fm_dict, body, triggers). fm_dict always has at least 'name'.
    """
    fm, body = parse_frontmatter(text)
    if fm is not None:
        name = str(fm.get("name", "")).strip()
        desc = str(fm.get("description", "")).strip() if isinstance(fm.get("description"), str) else ""
        desc = " ".join(desc.split())  # flatten block scalars
        triggers = []
        if "trigger_phrases" in fm:
            tp = fm["trigger_phrases"]
            if isinstance(tp, str):
                triggers = [t.strip().lower() for t in tp.split(",") if t.strip()]
            elif isinstance(tp, list):
                triggers = [str(t).strip().lower() for t in tp if t]
        if not desc:
            # pull first prose paragraph from body as fallback
            para = re.search(r"\n\n([A-Z][^\n]{40,})", body)
            if para:
                desc = " ".join(para.group(1).split())[:300]
        # filter triggers to remove generic collisions
        triggers = [t for t in triggers if t not in _GENERIC]
        # canonicalize name
        if not name:
            m = re.search(r"\*\*Folder\*\*\s*:\s*(.+)", text)
            name = m.group(1).strip() if m else "offensive-unnamed"
        return fm, body, triggers
    else:
        fm, body, triggers = parse_legacy(text)
        return fm, body, triggers


def canonical_name(category: str, folder: str, fm_name: str | None) -> str:
    """The skill's registry id = offensive-<folder-name>."""
    if fm_name and fm_name.startswith("offensive-"):
        base = fm_name
    elif fm_name:
        base = fm_name
    else:
        base = folder
    base = base.strip()
    if base.startswith("offensive-"):
        return base
    return "offensive-" + base


def make_aliases(name: str, triggers: list[str], desc: str) -> list[str]:
    """Aliases for this skill. Per namespace-separation policy (Option A):
    - Always include the full canonical name (offensive-<name>).
    - Include the folder stem without prefix (e.g. sqli for offensive-sqli).
    - Add distinctive long trigger tokens (NOT generic short tokens like sqli,
      ssrf — those belong to core OpenCode skills per first-wins policy).
    """
    aliases = [name]
    core = re.sub(r"^offensive-", "", name)
    if core and core != name and core not in aliases:
        aliases.append(core)

    added = {name, core}
    # only keep trigger tokens that are distinctive (>=3 chars, not generic)
    for t in triggers:
        t = re.sub(r"[^a-z0-9\-]+", "-", t.lower()).strip("-")
        if t and len(t) >= 3 and t not in _GENERIC and t not in added:
            aliases.append(t)
            added.add(t)
        if len(aliases) >= 10:
            break
    return aliases


def make_tags(name: str, category: str) -> list[str]:
    """Tags for the registry 'tags' dict and skill metadata."""
    parts = [p for p in re.split(r"[-_]", name) if p and p != "offensive"]
    defaults = {
        "ad": ["ad", "windows", "domain"], "ai": ["ai", "llm"],
        "api": ["api", "rest"], "auth": ["auth", "tokens"],
        "cicd": ["cicd", "pipeline"], "cloud": ["cloud"],
        "container": ["container", "docker", "k8s"],
        "crypto": ["crypto"], "exploit-dev": ["exploit-dev"],
        "fuzzing": ["fuzzing"], "infrastructure": ["infrastructure", "redteam"],
        "iot": ["iot", "embedded"], "mobile": ["mobile"],
        "network": ["network", "wifi"],
        "post-exploitation": ["post-exploit"], "privesc": ["privesc"],
        "recon": ["recon", "osint"],
        "social-engineering": ["social-engineering"],
        "supply-chain": ["supply-chain"], "utility": ["utility"],
        "web": ["web"], "wireless": ["wireless"],
    }
    return list(dict.fromkeys(defaults.get(category, []) + parts))


# ── canonical route key → set of distinctive tokens to route-joined skills ──
ROUTE_TOKENS = {
    "sql injection": ["sqli"],
    "sqli": ["sqli"],
    "ssrf": ["ssrf"],
    "xss": ["xss"],
    "idor": ["idor"],
    "open redirect": ["open-redirect", "redirect"],
    "csrf": ["csrf"],
    "lfi": ["lfi"],
    "ssti": ["ssti"],
    "command injection": ["rce"],
    "rce": ["rce"],
    "file upload": ["file-upload", "upload"],
    "request smuggling": ["request-smuggling", "smuggling"],
    "waf bypass": ["waf-bypass", "bypass"],
    "xxe": ["xxe"],
    "race condition": ["race-condition", "toctou"],
    "parameter pollution": ["parameter-pollution"],
    "jwt": ["jwt"],
    "oauth": ["oauth"],
    "graphql": ["graphql"],
    "business logic": ["business-logic"],
    "deserialization": ["deserialization"],
    "path traversal": ["lfi"],
    "authentication": ["auth", "jwt", "oauth"],
    "authorization": ["idor"],
    "cloud": ["cloud"],
    "kubernetes": ["k8s"],
    "ai": ["ai", "llm"],
    "dependency": ["dependency-confusion", "supply-chain"],
    "ssrf": ["ssrf"],
}


def process_tree() -> list[dict]:
    """Walk Claude-Red/Skills, return list of skill metadata dicts."""
    skills = []
    FLAT_DIR.mkdir(parents=True, exist_ok=True)
    for cat_dir in sorted(SRC_TREE.iterdir()):
        if not cat_dir.is_dir():
            continue
        category = cat_dir.name
        for skill_dir in sorted(cat_dir.iterdir()):
            md = skill_dir / "SKILL.md"
            if not md.is_file():
                continue
            text = md.read_text(encoding="utf-8", errors="replace")
            fm, body, triggers = parse_any(text)
            fm_name = str(fm.get("name") or skill_dir.name)
            name = canonical_name(category, skill_dir.name, fm_name)
            desc = str(fm.get("description", "")).strip()
            if not desc:
                # fallback: first prose paragraph from body
                para = re.search(r"\n\n([A-Z][^\n]{40,})", "\n\n" + body)
                desc = " ".join(para.group(1).split())[:300] if para else \
                       f"{name} — offensive security methodology."
            desc = " ".join(desc.split())
            phase = PHASE_BY_CATEGORY.get(category, "exploit")
            aliases = make_aliases(name, triggers, desc)
            tags = make_tags(name, category)
            source = str(md.relative_to(ROOT))

            # Write normalised flat file
            fm_out = {
                "id": name,
                "name": name,
                "description": desc,
                "phase": phase,
                "severity": "info",
                "category": category,
                "aliases": aliases,
                "tags": tags,
                "source": source,
            }
            flat_text = yaml.safe_dump(fm_out, default_flow_style=False,
                                       sort_keys=False, allow_unicode=True)
            flat_path = FLAT_DIR / f"{category}-{skill_dir.name}.md"
            flat_path.write_text(
                f"---\n{flat_text}---\n{body.lstrip()}\n",
                encoding="utf-8", newline="\n",
            )

            # Runtime copy
            rt_dir = RUNTIME / name
            rt_dir.mkdir(parents=True, exist_ok=True)
            (rt_dir / "SKILL.md").write_text(
                f"---\n{flat_text}---\n{body.lstrip()}\n",
                encoding="utf-8", newline="\n",
            )

            skills.append({
                "name": name,
                "display_name": fm_name or name,
                "desc": desc,
                "phase": phase,
                "category": category,
                "aliases": aliases,
                "tags": tags,
                "source": source,
                "file": str(flat_path.relative_to(ROOT)),
            })
    return skills


def update_catalog(skills: list[dict]) -> dict:
    """Write the independent Claude-Red catalog.

    Claude-Red aliases intentionally live in their own namespace. This keeps
    core aliases such as ``sqli`` authoritative in runtime/registry.json while
    still making every offensive playbook directly resolvable here.
    """
    reg = {
        "version": "1.0.0",
        "generated_at": "integrate_claude_red.py",
        "paths": {
            "skills_source": "skills/claude-red",
            "skills_runtime": ".agents/skills",
        },
        "skills": {},
        "aliases": {},
        "routes": {},
        "agents": {},
        "commands": {},
        "tags": {},
    }

    # Track aliases within this catalog using deterministic first-wins order.
    claimed: dict[str, str] = {}
    for sk in skills:
        sid = sk["name"]
        kept = [sid]
        claimed[sid.lower()] = sid
        reg["aliases"][sid.lower()] = sid
        for a in sk["aliases"]:
            a_l = a.lower()
            if a_l not in claimed and a_l != sid.lower():
                claimed[a_l] = sid
                reg["aliases"][a_l] = sid
                kept.append(a)
        sk["aliases"] = kept  # truncate to winning-only

    for sk in skills:
        sid = sk["name"]
        reg["skills"][sid] = {
            "id": sid,
            "name": sid,
            "description": sk["desc"],
            "file": sk["file"],
            "phase": sk["phase"],
            "severity": "info",
            "aliases": sk["aliases"],
            "tags": sk["tags"],
            "category": sk["category"],
            "source": sk["source"],
        }

    # Tags index
    for sk in skills:
        for t in sk["tags"]:
            reg["tags"].setdefault(t, [])
            if sk["name"] not in reg["tags"][t]:
                reg["tags"][t].append(sk["name"])

    # Category routes make the catalog useful without polluting core routes.
    for category in sorted({sk["category"] for sk in skills}):
        reg["routes"][category] = [
            sk["name"] for sk in skills if sk["category"] == category
        ]

    # Catch-all route LAST so specific category routes win during resolution.
    reg["routes"]["full"] = [sk["name"] for sk in skills]

    # Deduplicate route lists
    for route, sids in reg["routes"].items():
        seen = []
        for s in sids:
            if s and s not in seen:
                seen.append(s)
        reg["routes"][route] = seen

    CLAUDE_RED_REG.write_text(json.dumps(reg, indent=2) + "\n", encoding="utf-8")
    return reg


def _read_pin_file() -> dict:
    """Read Claude-Red/.upstream-pin (upstream URL + pinned revision)."""
    pin_path = SRC_TREE.parent / ".upstream-pin"
    data: dict = {}
    if not pin_path.exists():
        return data
    for line in pin_path.read_text(encoding="utf-8").splitlines():
        if ":" in line:
            key, _, val = line.partition(":")
            data[key.strip()] = val.strip()
    return data


def write_provenance(skills: list[dict]) -> dict:
    """Record the pinned source revision + output inventory.

    This makes the integration reproducible: a future run can confirm the
    upstream Claude-Red revision and the exact set of derived artifacts without
    guessing. Nothing here deletes or rewrites source folders.
    """
    pin = _read_pin_file()
    prov: dict = {
        "generated_by": "scripts/integrate_claude_red.py",
        "source_tree": str(SRC_TREE.relative_to(ROOT)),
        "source_upstream": pin.get("upstream"),
        "source_revision": pin.get("revision"),
        "source_describe": pin.get("describe"),
        "source_date": pin.get("date"),
        "skill_count": len(skills),
        "categories": sorted({sk["category"] for sk in skills}),
        "outputs": {
            "flat": str(FLAT_DIR.relative_to(ROOT)),
            "runtime": str(RUNTIME.relative_to(ROOT)),
            "catalog": str(CLAUDE_RED_REG.relative_to(ROOT)),
        },
        "skills": sorted(sk["name"] for sk in skills),
    }
    # Best-effort: if a nested Claude-Red git clone is present, prefer live data.
    if (SRC_TREE.parent / ".git").exists():
        try:
            rev = subprocess.run(["git", "rev-parse", "HEAD"],
                                 cwd=str(SRC_TREE.parent), capture_output=True,
                                 text=True, timeout=10)
            if rev.returncode == 0:
                prov["source_revision"] = rev.stdout.strip()
            describe = subprocess.run(["git", "describe", "--tags", "--always"],
                                      cwd=str(SRC_TREE.parent), capture_output=True,
                                      text=True, timeout=10)
            if describe.returncode == 0:
                prov["source_describe"] = describe.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            pass
    PROVENANCE.write_text(json.dumps(prov, indent=2) + "\n", encoding="utf-8")
    return prov


def main() -> int:
    print(f"🌳 Reading Claude-Red source tree: {SRC_TREE}")
    skills = process_tree()
    print(f"✅ Generated {len(skills)} normalised flat files in {FLAT_DIR}")
    print(f"✅ Wrote {len(skills)} runtime copies in {RUNTIME}")

    print("📝 Writing runtime/registry-claude-red.json...")
    reg = update_catalog(skills)
    print(f"✅ Registry: {len(reg['skills'])} skills, "
          f"{len(reg['aliases'])} aliases, {len(reg['tags'])} tags, "
          f"{len(reg['routes'])} routes")

    prov = write_provenance(skills)
    print(f"📍 Provenance: {PROVENANCE.relative_to(ROOT)} "
          f"(source {prov.get('source_describe') or 'unversioned'})")

    print("\n🔎 Running skill_registry.py check...")
    r = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "skill_registry.py"), "check"],
        capture_output=True, text=True, cwd=str(ROOT),
    )
    print(r.stdout)
    if r.returncode != 0:
        print("STDERR:", r.stderr[-1500:])
        return r.returncode
    return 0


if __name__ == "__main__":
    sys.exit(main())
