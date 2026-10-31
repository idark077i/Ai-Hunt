#!/usr/bin/env python3
"""skill_registry.py — deterministic scenario -> skills resolver.

Subcommands:
  check      validate registry consistency (skills exist, aliases resolve,
             routes reference known skills, runtime copies in sync)
  resolve    <scenario> -> ordered list of skill ids + playbook paths
  generate   write .agents/skills/<id>/SKILL.md from skills/<id>.md
  list       dump registry (skills / aliases / routes / agents / commands)

Exit codes: 0 ok, 1 check failure, 2 usage/parse error, 3 missing files.
No side effects beyond `generate`.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REGISTRY_PATH = ROOT / "runtime" / "registry.json"
CATALOG_PATH = ROOT / "runtime" / "registry-claude-red.json"
SKILLS_SOURCE = ROOT / "skills"
SKILLS_RUNTIME = ROOT / ".agents" / "skills"

TOKEN_RE = re.compile(r"[a-z0-9]+")


def _load_registry() -> dict:
    if not REGISTRY_PATH.exists():
        sys.exit(f"registry not found: {REGISTRY_PATH}")
    try:
        return json.loads(REGISTRY_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        sys.exit(f"registry is not valid JSON: {exc}")


def _load_catalog() -> dict | None:
    """Load the optional out-of-tree Claude-Red catalog.

    The core registry stays small and curated; the offensive catalog is a
    second, independently generated tier. Absence is not an error.
    """
    if not CATALOG_PATH.exists():
        return None
    try:
        return json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        sys.exit(f"catalog is not valid JSON: {exc}")


def _tier_namespaces(registry: dict, catalog: dict | None) -> tuple[set[str], set[str]]:
    """Return (core_sids, catalog_sids) for namespace-aware resolution."""
    core = set(registry.get("skills", {}))
    cat = set((catalog or {}).get("skills", {}))
    return core, cat


def _skill_file(registry: dict, skill_id: str) -> Path:
    return ROOT / registry["skills"][skill_id]["file"]


def _tokens(*values: str) -> set[str]:
    return {t for v in values if v for t in TOKEN_RE.findall(v.lower())}


def check(registry: dict, catalog: dict | None = None) -> list[str]:
    """Return list of problems (empty = healthy).

    Validates the curated core registry, and — when present — the separate
    Claude-Red catalog. The catalog may share alias strings with the core
    registry (they are distinct namespaces), so its checks are self-contained.
    """
    problems: list[str] = []
    problems.extend(_check_tier(registry, label="registry"))
    if catalog is not None:
        problems.extend(_check_tier(catalog, label="catalog"))
    return problems


def _check_tier(registry: dict, label: str = "registry") -> list[str]:
    """Validate one registry tier in isolation."""
    problems: list[str] = []
    skills = registry.get("skills", {})
    aliases = registry.get("aliases", {})
    routes = registry.get("routes", {})
    agents = registry.get("agents", {})
    commands = registry.get("commands", {})

    if not skills:
        problems.append(f"{label}: has no skills")

    for sid, meta in skills.items():
        f = ROOT / meta.get("file", "")
        if not f.exists():
            problems.append(f"{label} skill '{sid}': file missing -> {f}")
        for field in ("name", "phase", "severity", "aliases"):
            if field not in meta:
                problems.append(f"{label} skill '{sid}': missing field '{field}'")
        for alias in meta.get("aliases", []):
            if aliases.get(alias) != sid:
                problems.append(f"{label} skill '{sid}': alias '{alias}' not mapped to it in aliases")

    for alias, sid in aliases.items():
        if sid not in skills:
            problems.append(f"{label} alias '{alias}' -> unknown skill '{sid}'")
        elif alias not in skills[sid].get("aliases", []):
            problems.append(f"{label} alias '{alias}' not declared in skill '{sid}' aliases")

    for route, sids in routes.items():
        if not sids:
            problems.append(f"{label} route '{route}' is empty")
        for sid in sids:
            if sid not in skills:
                problems.append(f"{label} route '{route}' -> unknown skill '{sid}'")

    for aid, meta in agents.items():
        f = ROOT / meta.get("file", "")
        if not f.exists():
            problems.append(f"{label} agent '{aid}': file missing -> {f}")
        for sid in meta.get("skills", []):
            if sid != "*" and sid not in skills:
                problems.append(f"{label} agent '{aid}': unknown skill '{sid}'")

    for cid, meta in commands.items():
        f = ROOT / meta.get("file", "")
        if not f.exists():
            problems.append(f"{label} command '{cid}': file missing -> {f}")
        for rid in meta.get("routes", []):
            if rid not in routes:
                problems.append(f"{label} command '{cid}': unknown route '{rid}'")

    for sid in skills:
        runtime_copy = SKILLS_RUNTIME / sid / "SKILL.md"
        source = ROOT / skills[sid].get("file", "")
        if not source.exists():
            continue
        if not runtime_copy.exists():
            problems.append(f"{label} runtime copy missing: {runtime_copy} (run 'generate')")
        elif runtime_copy.read_text(encoding="utf-8") != source.read_text(encoding="utf-8"):
            problems.append(f"{label} runtime copy out of sync: {runtime_copy} (run 'generate')")

    return problems


def resolve(registry: dict, scenario: str) -> list[str]:
    """Ordered skill ids for a scenario. Exact route -> alias -> fuzzy tokens."""
    scenario = scenario.strip().lower()
    routes = registry.get("routes", {})
    skills = registry.get("skills", {})

    if scenario in routes:
        return list(routes[scenario])

    if scenario in registry.get("aliases", {}):
        sid = registry["aliases"][scenario]
        for route, sids in routes.items():
            if sid in sids:
                return list(sids)
        return [sid]

    q = _tokens(scenario)
    if not q:
        return []
    best: list[tuple[int, str]] = []
    for sid, meta in skills.items():
        score = len(q & _tokens(sid, meta["name"], *meta.get("tags", [])))
        if score:
            best.append((score, sid))
    best.sort(key=lambda t: (-t[0], t[1]))
    return [sid for _, sid in best]


def resolve_exact(registry: dict, scenario: str, expand_alias: bool = True) -> list[str]:
    """Exact-match resolution only: route -> alias -> skill id.

    Returns [] when the scenario is not an exact route/alias/id. Used by the
    two-tier resolver so a core fuzzy hit cannot shadow an exact catalog id.

    expand_alias: when True an alias match expands to the alias's route (core
    behaviour). When False an alias match returns only that skill — used for
    the catalog, whose category routes are far too broad for expansion.
    """
    scenario = scenario.strip().lower()
    routes = registry.get("routes", {})
    skills = registry.get("skills", {})

    if scenario in routes:
        return list(routes[scenario])
    if scenario in registry.get("aliases", {}):
        sid = registry["aliases"][scenario]
        if expand_alias:
            for route, sids in routes.items():
                if sid in sids:
                    return list(sids)
        return [sid]
    if scenario in skills:
        return [scenario]
    return []


def resolve_two_tier(
    registry: dict,
    catalog: dict | None,
    scenario: str,
    mode: str = "all",
    with_foundation: bool = False,
) -> tuple[list[str], dict]:
    """Namespace-aware resolution across the core registry and the catalog.

    Order: exact match in core -> exact match in catalog -> fuzzy core ->
    fuzzy catalog. Returns (skill_ids, owning_tier_registry).
    """
    # 1. Exact match, core first (route-expanding), then catalog (skill-only).
    exact = resolve_exact(registry, scenario, expand_alias=True)
    if exact:
        return exact, registry
    if catalog is not None:
        exact_cat = resolve_exact(catalog, scenario, expand_alias=False)
        if exact_cat:
            return exact_cat, catalog
    # 2. Fuzzy match, core first.
    for tier in (registry, catalog):
        if tier is None:
            continue
        fuzzy = resolve(tier, scenario)
        if fuzzy:
            return fuzzy, tier
    return [], registry


def resolve_with_options(
    registry: dict,
    scenario: str,
    mode: str = "all",
    with_foundation: bool = False,
) -> list[str]:
    """Resolve with the skill-gate options used by the hunt flow.

    mode: 'recon' keeps recon-phase skills only; 'test'/'hunt' keeps
          exploit-phase skills only; 'all' keeps everything.
    with_foundation: prepend the foundation skills (route 'start'), deduped.
    """
    skills = registry.get("skills", {})
    sids = resolve(registry, scenario)
    if mode in ("recon", "test", "hunt"):
        wanted = "recon" if mode == "recon" else "exploit"
        sids = [sid for sid in sids if skills.get(sid, {}).get("phase") == wanted]
    if with_foundation:
        foundation = list(registry.get("routes", {}).get("start", []))
        sids = foundation + [sid for sid in sids if sid not in foundation]
    return sids


def generate(registry: dict, skill_ids: list[str] | None = None) -> list[Path]:
    """Copy skill sources to .agents/skills/<id>/SKILL.md. Returns written paths."""
    skills = registry["skills"]
    ids = skill_ids or list(skills)
    missing = [sid for sid in ids if sid not in skills]
    if missing:
        sys.exit(f"unknown skill ids: {', '.join(missing)}")
    written: list[Path] = []
    for sid in ids:
        src = _skill_file(registry, sid)
        if not src.exists():
            sys.exit(f"source missing for skill '{sid}': {src}")
        dst = SKILLS_RUNTIME / sid / "SKILL.md"
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(src.read_text(encoding="utf-8"), encoding="utf-8")
        written.append(dst)
    return written


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Deterministic scenario -> skills resolver")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_check = sub.add_parser("check", help="validate registry consistency")
    p_check.add_argument("--tier", choices=["core", "catalog", "all"], default="all",
                         help="which tier to validate (default: all)")
    p_resolve = sub.add_parser("resolve", help="resolve a scenario to skill ids")
    p_resolve.add_argument("scenario", nargs="?", help="scenario (positional or --scenario)")
    p_resolve.add_argument("--scenario", dest="scenario_flag", help="scenario as a flag (alias)")
    p_resolve.add_argument("--mode", choices=["all", "recon", "test", "hunt",
                                              "update", "report"],
                           default="all",
                           help="phase filter (test/hunt -> exploit phase; "
                                "update/report -> all phases)")
    p_resolve.add_argument("--paths-only", action="store_true",
                           help="print only the resolved playbook file paths")
    p_resolve.add_argument("--with-foundation", action="store_true",
                           help="prepend foundation skills (route 'start')")
    p_resolve.add_argument("--fallback", metavar="ROUTE",
                           help="route id to use when the scenario resolves to "
                                "nothing, so a vague goal never dead-ends the "
                                "hunt (e.g. --fallback full). Without this, a "
                                "miss exits 2.")
    p_gen = sub.add_parser("generate", help="write .agents/skills runtime copies")
    p_gen.add_argument("--skills", nargs="*", default=None)
    p_gen.add_argument("--tier", choices=["core", "catalog", "all"], default="core",
                       help="which tier to materialise (default: core)")
    p_list = sub.add_parser("list", help="dump registry")
    p_list.add_argument("--section", choices=["skills", "aliases", "routes", "agents", "commands"])
    p_list.add_argument("--tier", choices=["core", "catalog"], default="core")

    args = parser.parse_args(argv)
    registry = _load_registry()
    catalog = _load_catalog()

    if args.cmd == "check":
        tiers = {
            "core": [(registry, "registry")],
            "catalog": [(catalog, "catalog")] if catalog else [],
            "all": [(registry, "registry")] + ([(catalog, "catalog")] if catalog else []),
        }[args.tier]
        problems: list[str] = []
        for tier_reg, label in tiers:
            if tier_reg is None:
                continue
            problems.extend(_check_tier(tier_reg, label=label))
        for p in problems:
            print(f"[FAIL] {p}")
        core_n = len(registry.get("skills", {}))
        cat_n = len(catalog.get("skills", {})) if catalog else 0
        print(f"checked: {core_n} core skills, {cat_n} catalog skills, "
              f"{len(registry.get('routes', {}))} core routes, "
              f"{len(registry.get('aliases', {}))} core aliases")
        return 1 if problems else 0

    if args.cmd == "resolve":
        scenario = args.scenario or args.scenario_flag
        if not scenario:
            print("resolve: missing scenario", file=sys.stderr)
            return 2
        sids, tier = resolve_two_tier(registry, catalog, scenario, mode=args.mode)
        # Apply phase filtering + foundation against the owning tier.
        skills = tier.get("skills", {})
        if args.mode in ("recon", "test", "hunt"):
            wanted = "recon" if args.mode == "recon" else "exploit"
            sids = [sid for sid in sids if skills.get(sid, {}).get("phase") == wanted]
        if args.with_foundation:
            foundation = list(tier.get("routes", {}).get("start", []))
            if foundation:
                sids = foundation + [sid for sid in sids if sid not in foundation]
        if not sids and args.fallback:
            route = registry.get("routes", {}).get(args.fallback)
            if not route:
                print(f"resolve: unknown fallback route '{args.fallback}' "
                      f"(known: {', '.join(sorted(registry.get('routes', {})))})",
                      file=sys.stderr)
                return 2
            sids = list(route)
            if args.mode in ("recon", "test", "hunt"):
                wanted = "recon" if args.mode == "recon" else "exploit"
                sids = [sid for sid in sids
                        if registry["skills"].get(sid, {}).get("phase") == wanted]
            print(f"resolve: no exact scenario match for '{scenario}' - "
                  f"fell back to route '{args.fallback}'", file=sys.stderr)
        if not sids:
            print(f"resolve: no skills for scenario '{scenario}'", file=sys.stderr)
            return 2
        for sid in sids:
            path = ROOT / tier["skills"][sid]["file"]
            print(path if args.paths_only else f"{sid}\t{path}")
        return 0

    if args.cmd == "generate":
        regs = {
            "core": [registry],
            "catalog": [catalog] if catalog else [],
            "all": [registry] + ([catalog] if catalog else []),
        }[args.tier]
        written = []
        for tier_reg in regs:
            written.extend(generate(tier_reg, args.skills))
        for dst in written:
            print(f"[gen] {dst}")
        return 0

    if args.cmd == "list":
        data_src = catalog if (args.tier == "catalog" and catalog) else registry
        section = args.section
        if section is None:
            print(json.dumps(data_src, indent=2, ensure_ascii=False))
            return 0
        data = data_src.get(section, {})
        for key in sorted(data):
            print(f"{key}\t{json.dumps(data[key], ensure_ascii=False)}")
        return 0

    return 2


if __name__ == "__main__":
    sys.exit(main())