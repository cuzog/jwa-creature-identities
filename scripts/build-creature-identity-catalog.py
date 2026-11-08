"""Publish only identities supported by game GUIDs, rarity, stats and ability GUIDs."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess
import sys

PALEO = "https://www.paleo.gg/games/jurassic-world-alive/dinodex"
SCRIPT = Path(__file__).with_name("fetch-game-creature-identities.py")


def fetch_page(url):
    response = subprocess.run(
        ["curl", "--fail", "--silent", "--show-error", "--location", "--retry", "2",
         "--max-time", "60", url], check=True, capture_output=True, text=True,
    )
    match = re.search(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
                      response.stdout, re.S)
    if match is None:
        raise ValueError(f"Paleo page data missing: {url}")
    return json.loads(match[1])["props"]["pageProps"]


def signature(rarity, stats):
    return rarity.lower(), tuple(stats)


def regular_moves(detail):
    return sorted({guid for move in detail["moves"]
                   for guid in re.findall(r"[a-f0-9]{32}", move["uuid"])})


def resolve_identities(game, roster, details):
    index = {}
    for creature in roster:
        key = signature(creature["rarity"], [creature[field] for field in
                        ("health", "damage", "speed", "armor", "crit", "critm")])
        index.setdefault(key, []).append(creature)
    identities = {}
    for creature in game["creatures"]:
        candidates = index.get(signature(creature["rarity"], creature["stats"]), [])
        matched = [candidate for candidate in candidates
                   if creature["moves"] and regular_moves(details[candidate["uuid"]]) == creature["moves"]]
        if len(matched) == 1:
            identities[creature["id"]] = {
                "displayName": matched[0]["name"], "rarity": creature["rarity"],
            }
    return identities


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="data")
    parser.add_argument("--package-directory", default="artifacts/game-identity-data")
    args = parser.parse_args()
    output = Path(args.output)
    catalog_path = output / "creature-identities.json"
    game_path = output / "game-creatures.json"
    previous = json.loads(catalog_path.read_text()) if catalog_path.exists() else None
    version = subprocess.check_output([sys.executable, str(SCRIPT), "--check-version"], text=True).strip()
    page = fetch_page(PALEO)
    paleo_updated = page["meta"]["lastModifiedDate"]
    if (previous and previous["gameVersion"] == version and
            previous["paleoUpdatedAt"] == paleo_updated and game_path.exists()):
        print(f"Unchanged: game {version}, Paleo {paleo_updated}; no package download")
        return

    if previous and previous["gameVersion"] == version and game_path.exists():
        game = json.loads(game_path.read_text())
    else:
        extracted = subprocess.check_output(
            [sys.executable, str(SCRIPT), args.package_directory], text=True,
        ).strip()
        game = json.loads(Path(extracted).read_text())
    if game["version"] != version:
        raise ValueError("Game version changed during generation; retry the job")
    roster = page["dex"]["items"]
    signatures = {signature(creature["rarity"], creature["stats"]) for creature in game["creatures"]}
    candidates = [creature for creature in roster if signature(creature["rarity"],
                  [creature[field] for field in ("health", "damage", "speed", "armor", "crit", "critm")]) in signatures]
    with ThreadPoolExecutor(max_workers=6) as pool:
        fetched = list(pool.map(lambda creature: (creature["uuid"], fetch_page(f'{PALEO}/{creature["uuid"]}')["detail"]), candidates))
    identities = resolve_identities(game, roster, dict(fetched))
    if not identities:
        raise ValueError("No identities verified; refusing to replace the published catalog")
    retained = previous["identities"] if previous else {}
    for guid, identity in identities.items():
        if guid in retained and retained[guid] != identity:
            raise ValueError(f"Identity changed for {guid}; manual review required")
    catalog = {
        "schemaVersion": "jwa-creature-identities.v1",
        "gameVersion": version, "packageSha256": game["sha256"],
        "paleoUpdatedAt": paleo_updated, "generatedAt": datetime.now(timezone.utc).isoformat(),
        "identities": dict(sorted({**retained, **identities}.items())),
    }
    output.mkdir(parents=True, exist_ok=True)
    # The workflow commits both files together, only after all validation succeeds.
    game_path.write_text(json.dumps(game, indent=2) + "\n")
    catalog_path.write_text(json.dumps(catalog, indent=2) + "\n")
    print(f"Verified {len(identities)} identities; published catalog has {len(catalog['identities'])}")


if __name__ == "__main__":
    main()
