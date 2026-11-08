import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from creature_game_mining import EXTRACTION_VERSION, change_report, creature_facts, progression_checks

spec = importlib.util.spec_from_file_location("catalog", Path(__file__).with_name("build-creature-identity-catalog.py"))
catalog = importlib.util.module_from_spec(spec)
spec.loader.exec_module(catalog)


class CatalogTests(unittest.TestCase):
    def test_composite_revenge_moves_are_compared_as_guids(self):
        normal, revenge = "a" * 32, "b" * 32
        self.assertEqual(catalog.regular_moves({"moves": [{"uuid": f"r__{normal}_{revenge}"}]}), [normal, revenge])

    def test_matching_stats_with_different_moves_is_not_an_identity(self):
        game = {"creatures": [{"id": "a" * 32, "rarity": "Epic", "stats": [1, 2, 3, 4, 5, 6], "moves": ["b" * 32]}]}
        creature = dict(uuid="test", name="Test", rarity="epic", health=1, damage=2, speed=3, armor=4, crit=5, critm=6)
        self.assertEqual(catalog.resolve_identities(game, [creature], {"test": {"moves": [{"uuid": "c" * 32}]}}), {})
        self.assertEqual(catalog.resolve_identities(game, [creature], {"test": {"moves": [{"uuid": "b" * 32}]}}), {"a" * 32: {"displayName": "Test", "rarity": "Epic"}})

    def test_ambiguous_matches_are_not_published(self):
        game = {"creatures": [{"id": "a" * 32, "rarity": "Epic", "stats": [1, 2, 3, 4, 5, 6], "moves": ["b" * 32]}]}
        first = dict(uuid="first", name="First", rarity="epic", health=1, damage=2, speed=3, armor=4, crit=5, critm=6)
        second = {**first, "uuid": "second", "name": "Second"}
        detail = {"moves": [{"uuid": "b" * 32}]}
        self.assertEqual(catalog.resolve_identities(game, [first, second], {"first": detail, "second": detail}), {})

    def test_unchanged_versions_skip_package_and_detail_downloads(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory)
            before = json.dumps({"gameVersion": "3.23.57", "paleoUpdatedAt": "2026-11-01", "extractionVersion": EXTRACTION_VERSION})
            (target / "creature-identities.json").write_text(before)
            (target / "game-creatures.json").write_text("{}")
            for name in ("game-changes.json", "progression-checks.json", "game-report.md"):
                (target / name).write_text("baseline")
            with patch.object(sys, "argv", ["catalog", "--output", directory]), \
                 patch.object(catalog.subprocess, "check_output", return_value="3.23.57\n") as command, \
                 patch.object(catalog, "fetch_page", return_value={"meta": {"lastModifiedDate": "2026-11-01"}}) as fetch:
                catalog.main()
                self.assertEqual(command.call_count, 1)
                self.assertEqual(fetch.call_count, 1)
                self.assertEqual((target / "creature-identities.json").read_text(), before)

    def test_adding_new_extracted_fields_establishes_a_baseline(self):
        old = {"version": "3.23", "creatures": [{"id": "a", "internalName": "Test", "stats": [10], "moves": []}]}
        new = {"version": "3.23", "sha256": "digest", "extractionVersion": EXTRACTION_VERSION,
               "rarityRules": {}, "creatures": [{**old["creatures"][0], "resistances": {"rs": 50}}]}
        report = change_report(old, new, {})
        self.assertTrue(report["baseline"])
        self.assertEqual(report["changedCreatures"], [])
        self.assertEqual(report["addedIds"], [])

    def test_changed_stats_and_removed_package_ids_are_reported(self):
        old = {"version": "3.23", "extractionVersion": EXTRACTION_VERSION,
               "creatures": [{"id": "a", "internalName": "Test", "stats": [10]}, {"id": "b", "internalName": "Removed", "stats": [20]}]}
        new = {"version": "3.24", "sha256": "digest", "rarityRules": {}, "creatures": [{"id": "a", "internalName": "Test", "stats": [11]}]}
        report = change_report(old, new, {"a": {"displayName": "Named Creature"}})
        self.assertFalse(report["baseline"])
        self.assertEqual(report["removedIds"], ["b"])
        self.assertEqual(report["changedCreatures"][0]["fields"], {"stats": {"before": [10], "after": [11]}})
        self.assertEqual(report["changedCreatures"][0]["displayName"], "Named Creature")

    def test_targeted_progression_reads_keep_costs_and_omega_unlocks(self):
        records = {
            "prestige": {"pll": [{"guid": "stage"}]},
            "stage": {"ull": 30, "dca": 100, "ulc": [{"rt": {"guid": "coins"}, "a": {"v": 150000}}], "pb": {"$type": "StEnBeDa, x", "st": "sp", "bp": 20000000}},
            "coins": {"devName": "SOFT_CASH"},
            "distribution": {"cap": {"guid": "caps"}, "app": {"guid": "gains"}},
            "caps": {"hp": 7000, "ap": 2000, "sp": 120, "def": 3499999},
            "gains": {"hp": 50, "ap": 15, "sp": 1, "def": 99999},
            "dna": {"cr": {"guid": "creature"}, "devName": "Test"},
            "milestones": {"mln": [{"cl": 6, "uc": [{"rt": {"guid": "dna"}, "a": {"v": 300}}], "msn": [{"$type": "MiNbADa, x", "ai": {"TypeName": "AbStDa", "guid": "ability"}}]}]},
        }
        definition = unittest.mock.Mock(side_effect=records.__getitem__)
        facts = creature_facts("creature", {"rl": [{"al": []}], "pll": {"guid": "prestige"}, "capd": {"guid": "distribution"}, "mlnc": {"guid": "milestones"}}, {"rs": 5000000}, definition)
        self.assertEqual(facts["resistances"]["rs"], 50)
        self.assertEqual(facts["enhancements"][0]["requirements"], [{"material": "coins", "amount": 150000}])
        self.assertEqual(facts["training"]["caps"]["armor"], 35)
        self.assertEqual(facts["training"]["perPoint"]["armor"], 1)
        self.assertEqual(facts["training"]["milestones"][0]["dnaCost"], 300)
        self.assertEqual(facts["training"]["milestones"][0]["abilityIds"], ["ability"])
        self.assertEqual(definition.call_count, 8)

    def test_progression_differences_are_reported_without_changing_identity(self):
        identities = {"a": {"displayName": "Test", "rarity": "Apex"}}
        game = {"version": "3.23", "creatures": [{"id": "a", "enhancements": [{"level": 1, "dnaCost": 100, "requirements": []}], "training": None}]}
        detail = {"test": {"enhancements": [{"cost": 200, "req": []}]}}
        result = progression_checks(game, identities, [{"name": "Test", "uuid": "test"}], detail)
        self.assertEqual(result["checkedFields"], 1)
        self.assertEqual(result["mismatches"][0]["field"], "enhancementCosts")
        self.assertEqual(identities["a"], {"displayName": "Test", "rarity": "Apex"})


if __name__ == "__main__":
    unittest.main()
