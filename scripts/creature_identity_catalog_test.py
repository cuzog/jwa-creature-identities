import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

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
            before = json.dumps({"gameVersion": "3.23.57", "paleoUpdatedAt": "2026-11-01"})
            (target / "creature-identities.json").write_text(before)
            (target / "game-creatures.json").write_text("{}")
            with patch.object(sys, "argv", ["catalog", "--output", directory]), \
                 patch.object(catalog.subprocess, "check_output", return_value="3.23.57\n") as command, \
                 patch.object(catalog, "fetch_page", return_value={"meta": {"lastModifiedDate": "2026-11-01"}}) as fetch:
                catalog.main()
                self.assertEqual(command.call_count, 1)
                self.assertEqual(fetch.call_count, 1)
                self.assertEqual((target / "creature-identities.json").read_text(), before)


if __name__ == "__main__":
    unittest.main()
