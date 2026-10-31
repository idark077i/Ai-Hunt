#!/usr/bin/env python3
"""Tests for scripts/skill_registry.py — deterministic resolver + registry integrity."""

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts import skill_registry as sr  # noqa: E402


class TestRegistryIntegrity(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = sr._load_registry()

    def test_registry_parses(self):
        self.assertIsInstance(self.registry, dict)
        self.assertEqual(self.registry["version"], "1.0.0")

    def test_check_passes(self):
        problems = sr.check(self.registry)
        self.assertEqual(problems, [], f"registry problems: {problems}")

    def test_skill_count_within_budget(self):
        n = len(self.registry["skills"])
        self.assertGreaterEqual(n, 10)
        self.assertLessEqual(n, 20)

    def test_all_aliases_bidirectional(self):
        for alias, sid in self.registry["aliases"].items():
            self.assertIn(sid, self.registry["skills"])
            self.assertIn(alias, self.registry["skills"][sid]["aliases"])

    def test_phases_and_severities_valid(self):
        for sid, meta in self.registry["skills"].items():
            self.assertIn(meta["phase"], ("recon", "exploit"))
            self.assertIn(meta["severity"], ("info", "low", "medium", "high", "critical"))

    def test_routes_reference_known_skills(self):
        known = set(self.registry["skills"])
        for route, sids in self.registry["routes"].items():
            self.assertTrue(sids, f"route {route} empty")
            for sid in sids:
                self.assertIn(sid, known, f"route {route} -> unknown {sid}")

    def test_every_skill_reachable_by_some_route(self):
        known = set(self.registry["skills"])
        reached = {s for sids in self.registry["routes"].values() for s in sids}
        self.assertEqual(known, reached)


class TestResolve(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = sr._load_registry()

    def test_exact_route(self):
        self.assertEqual(sr.resolve(self.registry, "web"), self.registry["routes"]["web"])

    def test_case_insensitive(self):
        self.assertEqual(sr.resolve(self.registry, "WEB"), self.registry["routes"]["web"])

    def test_alias_resolves_to_route(self):
        result = sr.resolve(self.registry, "osint")
        self.assertIn("dorking", result)

    def test_fuzzy_tokens(self):
        result = sr.resolve(self.registry, "sql injection")
        self.assertIn("sqli", result)

    def test_unknown_scenario_empty(self):
        self.assertEqual(sr.resolve(self.registry, "zzz-not-a-scenario"), [])

    def test_deterministic(self):
        a = sr.resolve(self.registry, "api")
        b = sr.resolve(self.registry, "api")
        self.assertEqual(a, b)

    def test_full_route_covers_all_skills(self):
        full = sr.resolve(self.registry, "full")
        self.assertEqual(set(full), set(self.registry["skills"]))


class TestResolveWithOptions(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = sr._load_registry()

    def test_foundation_skills_always_included(self):
        foundation = self.registry["routes"]["start"]
        for scenario in ("web", "api", "auth", "upload"):
            sids = sr.resolve_with_options(self.registry, scenario, with_foundation=True)
            self.assertTrue(set(foundation).issubset(set(sids)),
                            f"foundation missing for {scenario}: {set(foundation) - set(sids)}")
            self.assertEqual(sids[:len(foundation)], foundation)

    def test_mode_all_default(self):
        self.assertEqual(
            sr.resolve_with_options(self.registry, "web"),
            sr.resolve(self.registry, "web"),
        )

    def test_mode_recon_filters_phase(self):
        sids = sr.resolve_with_options(self.registry, "full", mode="recon")
        self.assertTrue(sids)
        for sid in sids:
            self.assertEqual(self.registry["skills"][sid]["phase"], "recon")

    def test_mode_test_keeps_exploit_only(self):
        sids = sr.resolve_with_options(self.registry, "full", mode="test")
        self.assertTrue(sids)
        for sid in sids:
            self.assertEqual(self.registry["skills"][sid]["phase"], "exploit")
        for sid in self.registry["routes"]["start"]:
            self.assertNotIn(sid, sids)

    def test_paths_only_resolves_to_existing_files(self):
        for scenario in ("web", "api", "graphql", "jwt", "file-upload"):
            paths = [
                ROOT / self.registry["skills"][sid]["file"]
                for sid in sr.resolve_with_options(self.registry, scenario, mode="test")
            ]
            self.assertTrue(paths, f"no skills for {scenario}")
            for p in paths:
                self.assertTrue(p.exists(), f"missing skill file: {p}")


class TestResolveCli(unittest.TestCase):
    def test_cli_paths_only_outputs_existing_files(self):
        from scripts.skill_registry import main as sr_main
        from io import StringIO
        import contextlib
        buf = StringIO()
        with contextlib.redirect_stdout(buf):
            code = sr_main(["resolve", "api", "--mode", "test", "--paths-only"])
        self.assertEqual(code, 0)
        for line in buf.getvalue().splitlines():
            self.assertTrue(Path(line.strip()).exists(), f"path does not exist: {line}")

    def test_cli_unknown_scenario_degrades_gracefully(self):
        from scripts.skill_registry import main as sr_main
        from io import StringIO
        import contextlib
        err = StringIO()
        with contextlib.redirect_stderr(err):
            code = sr_main(["resolve", "zzz-not-a-scenario", "--paths-only"])
        self.assertEqual(code, 2)
        self.assertIn("no skills", err.getvalue())

    def test_cli_vague_goal_falls_back_with_flag(self):
        """A vague goal must not dead-end the hunt when --fallback is given."""
        from scripts.skill_registry import main as sr_main
        from io import StringIO
        import contextlib
        out, err = StringIO(), StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = sr_main(["resolve", "find a loophole", "--mode", "test",
                            "--fallback", "full", "--paths-only"])
        self.assertEqual(code, 0, err.getvalue())
        self.assertIn("fell back to route 'full'", err.getvalue())
        paths = [p for p in out.getvalue().splitlines() if p.strip()]
        self.assertTrue(paths, "fallback must emit skill paths")
        for line in paths:
            self.assertTrue(Path(line.strip()).exists(), f"missing: {line}")

    def test_cli_unknown_fallback_route_errors(self):
        from scripts.skill_registry import main as sr_main
        from io import StringIO
        import contextlib
        err = StringIO()
        with contextlib.redirect_stderr(err):
            code = sr_main(["resolve", "zzz", "--fallback", "not-a-route",
                            "--paths-only"])
        self.assertEqual(code, 2)
        self.assertIn("unknown fallback", err.getvalue())

    def test_cli_foundation_flag(self):
        from scripts.skill_registry import main as sr_main
        from io import StringIO
        import contextlib
        buf = StringIO()
        with contextlib.redirect_stdout(buf):
            code = sr_main(["resolve", "web", "--with-foundation", "--paths-only"])
        self.assertEqual(code, 0)
        lines = buf.getvalue().splitlines()
        foundation = sr._load_registry()["routes"]["start"]
        for sid in foundation:
            self.assertTrue(any(Path(l).name == f"{sid}.md" for l in lines))


class TestGenerate(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = sr._load_registry()

    def test_generate_writes_runtime_copies(self):
        written = sr.generate(self.registry)
        self.assertEqual(len(written), len(self.registry["skills"]))
        for dst in written:
            self.assertTrue(dst.exists())
        problems = sr.check(self.registry)
        self.assertEqual(problems, [])

    def test_runtime_copy_matches_source(self):
        for sid, meta in self.registry["skills"].items():
            src = ROOT / meta["file"]
            dst = sr.SKILLS_RUNTIME / sid / "SKILL.md"
            self.assertEqual(dst.read_text(encoding="utf-8"),
                             src.read_text(encoding="utf-8"))


class TestFrontmatter(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.registry = sr._load_registry()

    def test_skill_frontmatter_matches_registry(self):
        for sid, meta in self.registry["skills"].items():
            text = (ROOT / meta["file"]).read_text(encoding="utf-8")
            self.assertTrue(text.startswith("---"), f"{sid}: missing frontmatter")
            fm = text.split("---", 2)[1]
            for field, want in (("id", sid), ("phase", meta["phase"]),
                                ("severity", meta["severity"])):
                self.assertIn(f"{field}: {want}", fm, f"{sid}: frontmatter {field} mismatch")
            self.assertIn(f"name: {meta['name']}", fm, f"{sid}: frontmatter name mismatch")


class TestClaudeRedCatalog(unittest.TestCase):
    """Two-tier integration: core registry stays curated; catalog is separate."""

    @classmethod
    def setUpClass(cls):
        cls.registry = sr._load_registry()
        cls.catalog = sr._load_catalog()

    def test_catalog_present(self):
        self.assertIsNotNone(self.catalog, "runtime/registry-claude-red.json missing")

    def test_core_registry_stays_curated(self):
        # The catalog must NOT inflate the core registry.
        n = len(self.registry["skills"])
        self.assertLessEqual(n, 20, f"core registry grew to {n}; catalog must stay separate")
        offensive = [s for s in self.registry["skills"] if s.startswith("offensive-")]
        self.assertEqual(offensive, [], "offensive skills leaked into the core registry")

    def test_catalog_self_consistent(self):
        problems = sr._check_tier(self.catalog, label="catalog")
        self.assertEqual(problems, [], f"catalog problems: {problems}")

    def test_catalog_ids_are_offensive_namespaced(self):
        for sid in self.catalog["skills"]:
            self.assertTrue(sid.startswith("offensive-"),
                            f"catalog skill not namespaced: {sid}")

    def test_catalog_files_exist(self):
        for sid, meta in self.catalog["skills"].items():
            self.assertTrue((ROOT / meta["file"]).exists(), f"catalog file missing: {sid}")

    def test_catalog_runtime_copies_exist(self):
        for sid in self.catalog["skills"]:
            self.assertTrue((sr.SKILLS_RUNTIME / sid / "SKILL.md").exists(),
                            f"runtime copy missing for catalog skill {sid}")

    def test_exact_catalog_id_resolves_to_itself(self):
        sids, tier = sr.resolve_two_tier(self.registry, self.catalog, "offensive-sqli")
        self.assertEqual(sids, ["offensive-sqli"])
        self.assertIs(tier, self.catalog)

    def test_short_alias_prefers_core(self):
        # 'sqli' must stay a core alias even though offensive-sqli is registered.
        sids, tier = sr.resolve_two_tier(self.registry, self.catalog, "sqli")
        self.assertIs(tier, self.registry)
        self.assertIn("sqli", sids)

    def test_catalog_distinctive_alias_reachable(self):
        sids, tier = sr.resolve_two_tier(self.registry, self.catalog, "netexec")
        self.assertIs(tier, self.catalog)
        self.assertEqual(sids, ["offensive-netexec"])

    def test_catalog_category_route_reachable(self):
        sids, tier = sr.resolve_two_tier(self.registry, self.catalog, "infrastructure")
        self.assertIs(tier, self.catalog)
        self.assertTrue(all(s.startswith("offensive-") for s in sids))
        self.assertTrue(sids)

    def test_catalog_full_route_covers_all_catalog_skills(self):
        full = sr.resolve(self.catalog, "full")
        self.assertEqual(set(full), set(self.catalog["skills"]))

    def test_catalog_bodies_are_not_truncated(self):
        """Regression: parse_legacy once kept only the title, gutting the body.

        Every catalog flat file must carry substantive body content beyond its
        frontmatter — a title-only body means the source methodology was dropped.
        """
        offenders = []
        for sid, meta in self.catalog["skills"].items():
            text = (ROOT / meta["file"]).read_text(encoding="utf-8")
            parts = text.split("---", 2)
            body = parts[2].strip() if len(parts) == 3 else ""
            # A real skill body is many lines; a heading alone is ~1-2 lines.
            if len(body.splitlines()) < 8:
                offenders.append(f"{sid} ({len(body.splitlines())} lines)")
        self.assertEqual(offenders, [],
                         f"catalog bodies look truncated: {offenders}")

    def test_catalog_body_preserves_source_methodology(self):
        """Legacy-format sources must keep their '## Full Methodology' content."""
        src_root = ROOT / "Claude-Red" / "Skills"
        if not src_root.exists():
            self.skipTest("pristine Claude-Red source not present")
        missing = []
        for sid, meta in self.catalog["skills"].items():
            flat = (ROOT / meta["file"]).read_text(encoding="utf-8")
            # find the matching source dir by suffix match on the flat stem
            stem = Path(meta["file"]).stem
            hits = [p for p in src_root.rglob("SKILL.md") if stem.endswith("-" + p.parent.name)]
            if len(hits) != 1:
                continue
            src_text = hits[0].read_text(encoding="utf-8", errors="replace")
            if "## Full Methodology" in src_text and "## Full Methodology" not in flat:
                missing.append(sid)
        self.assertEqual(missing, [],
                         f"skills lost their methodology body: {missing}")


if __name__ == "__main__":
    unittest.main(verbosity=2)