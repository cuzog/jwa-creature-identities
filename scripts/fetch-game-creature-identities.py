"""Read creature GUIDs, rarities, stats and ability GUIDs from the public game package."""
import hashlib
import argparse
from functools import lru_cache
import json
import re
import shutil
import subprocess
import time
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from zipfile import ZipFile
from creature_game_mining import EXTRACTION_VERSION, creature_facts, rarity_rules

source = "https://d.apkpure.net/b/XAPK/com.ludia.jw2?version=latest"


def latest_version():
    # Capture headers internally: redirect URLs contain temporary credentials.
    response = subprocess.run(
        ["curl", "--fail", "--silent", "--head", "--max-time", "30", source],
        check=True, capture_output=True, text=True,
    )
    location = re.search(r"^location:\s*(.+)$", response.stdout, re.I | re.M)
    if location is None:
        raise ValueError("Package provider did not return version metadata")
    filename = parse_qs(urlsplit(location[1].strip()).query).get("filename", [""])[0]
    version = re.search(r"_(\d+(?:\.\d+)+)_APKPure", filename)
    if version is None:
        raise ValueError("Package provider returned unrecognized version metadata")
    return version[1]


def extract_game(package, directory, expanded=True):
    started = time.perf_counter()
    cpu_started = time.process_time()
    reads = 0
    creatures = []
    rarities = {}
    with ZipFile(package) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        for member in archive.namelist():
            if not member.endswith(".apk"):
                continue
            apk = directory / Path(member).name
            if not apk.exists() or apk.stat().st_mtime < package.stat().st_mtime:
                with archive.open(member) as incoming, apk.open("wb") as outgoing:
                    shutil.copyfileobj(incoming, outgoing)
            with ZipFile(apk) as assets:
                databases = [name for name in assets.namelist() if name.endswith("/DataDatabase.json")]
                for database in databases:
                    prefix = database.split("Assets/Data/")[0]
                    mapping = json.loads(assets.read(database))["Data"]

                    @lru_cache(maxsize=None)
                    def definition(guid):
                        nonlocal reads
                        reads += 1
                        return json.loads(assets.read(prefix + mapping[guid]))

                    for guid, path in mapping.items():
                        if "/CreaturesStaticData/" not in path:
                            continue
                        creature = definition(guid)
                        rounds = creature.get("rl", [])
                        if len(rounds) != 1:
                            continue
                        rarity = definition(creature["r"]["guid"])
                        attributes = definition(rounds[0]["att"]["guid"])
                        row = {
                            "id": guid, "definition": path, "internalName": creature["dn"],
                            "localizationKey": creature["lkn"], "rarity": rarity["devName"],
                            "stats": [attributes.get("hp", 0), attributes.get("miap", 0),
                                      attributes.get("s", 0), attributes.get("def", 0) / 100000,
                                      attributes.get("chc", 0) / 100000, attributes.get("chm", 0) / 100000],
                            "moves": sorted({move[key]["guid"] for move in rounds[0]["al"] if move
                                             for key in ("ai", "rai", "otai")
                                             if move.get(key, {}).get("guid")}),
                        }
                        if expanded:
                            if rarity["devName"] not in rarities:
                                rarities[rarity["devName"]] = rarity_rules(rarity, definition)
                            row.update(creature_facts(guid, creature, attributes, definition))
                        creatures.append(row)
    with package.open("rb") as package_file:
        sha256 = hashlib.file_digest(package_file, "sha256").hexdigest()
    return {"source": source, "version": manifest["version_name"], "sha256": sha256,
            "extractionVersion": EXTRACTION_VERSION if expanded else 1,
            "creatures": creatures, "rarityRules": rarities,
            "extractionMetrics": {"wallSeconds": round(time.perf_counter() - started, 4),
                                  "cpuSeconds": round(time.process_time() - cpu_started, 4), "definitionReads": reads}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", nargs="?", default="artifacts/game-identity-data")
    parser.add_argument("--check-version", action="store_true")
    parser.add_argument("--cached-package", action="store_true", help="Extract the downloaded package without network access")
    args = parser.parse_args()
    if args.check_version:
        print(latest_version())
        return
    directory = Path(args.directory)
    directory.mkdir(parents=True, exist_ok=True)
    package = directory / "jwa-latest.xapk"
    cached_version = None
    if package.exists():
        with ZipFile(package) as archive:
            cached_version = json.loads(archive.read("manifest.json"))["version_name"]
    if args.cached_package and cached_version is None:
        raise ValueError("No cached game package is available")
    version = cached_version if args.cached_package else latest_version()
    if cached_version != version:
        temporary = package.with_suffix(".partial")
        # Redirect URLs contain temporary download credentials. Never print headers or URLs.
        subprocess.run(["curl", "--fail", "--location", "--silent", "--max-time", "600",
                        "--output", str(temporary), source], check=True)
        with ZipFile(temporary) as archive:
            if json.loads(archive.read("manifest.json"))["version_name"] != version:
                raise ValueError("Package version changed during download; retry the job")
        temporary.replace(package)
    report = extract_game(package, directory)
    result = directory / "creatures.json"
    result.write_text(json.dumps(report, indent=2) + "\n")
    print(result.resolve())


if __name__ == "__main__":
    main()
