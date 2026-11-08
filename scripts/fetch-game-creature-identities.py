"""Read creature GUIDs, rarities, stats and ability GUIDs from the public game package."""
import hashlib
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from zipfile import ZipFile

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


version = latest_version()
if "--check-version" in sys.argv:
    print(version)
    sys.exit(0)

directory = Path(sys.argv[1] if len(sys.argv) > 1 else "artifacts/game-identity-data")
directory.mkdir(parents=True, exist_ok=True)
package = directory / "jwa-latest.xapk"
cached_version = None
if package.exists():
    with ZipFile(package) as archive:
        cached_version = json.loads(archive.read("manifest.json"))["version_name"]
if cached_version != version:
    temporary = package.with_suffix(".partial")
    # Redirect URLs contain temporary download credentials. Never print headers or URLs.
    subprocess.run(["curl", "--fail", "--location", "--silent", "--max-time", "600",
                    "--output", str(temporary), source], check=True)
    with ZipFile(temporary) as archive:
        if json.loads(archive.read("manifest.json"))["version_name"] != version:
            raise ValueError("Package version changed during download; retry the job")
    temporary.replace(package)

creatures = []
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

                def definition(guid):
                    return json.loads(assets.read(prefix + mapping[guid]))

                for guid, path in mapping.items():
                    if "/CreaturesStaticData/" not in path:
                        continue
                    creature = definition(guid)
                    rounds = creature.get("rl", [])
                    if len(rounds) != 1:
                        continue
                    rarity = definition(creature["r"]["guid"])["devName"]
                    attributes = definition(rounds[0]["att"]["guid"])
                    creatures.append({
                        "id": guid, "definition": path, "internalName": creature["dn"],
                        "localizationKey": creature["lkn"], "rarity": rarity,
                        "stats": [attributes.get("hp", 0), attributes.get("miap", 0),
                                  attributes.get("s", 0), attributes.get("def", 0) / 100000,
                                  attributes.get("chc", 0) / 100000, attributes.get("chm", 0) / 100000],
                        "moves": sorted({move[key]["guid"] for move in rounds[0]["al"] if move
                                         for key in ("ai", "rai", "otai")
                                         if move.get(key, {}).get("guid")}),
                    })
with package.open("rb") as package_file:
    sha256 = hashlib.file_digest(package_file, "sha256").hexdigest()
report = {"source": source, "version": manifest["version_name"],
          "sha256": sha256,
          "creatures": creatures}
result = directory / "creatures.json"
result.write_text(json.dumps(report, indent=2) + "\n")
print(result.resolve())
