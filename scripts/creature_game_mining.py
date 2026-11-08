"""Small projections of creature records already selected by the identity extractor."""
EXTRACTION_VERSION = 2
RESISTANCES = ("rs", "rsp", "rt", "rr", "rd", "rdot", "rv", "rdec", "rcrit", "rarm", "raff", "rhd", "rdaze")
STAT_FIELDS = {"health": "hp", "damage": "ap", "speed": "sp", "armor": "def", "crit": "chc", "critm": "chm"}
MATERIALS = {"SOFT_CASH": "coins", "MINOR_CATALYST": "catalyst_minor", "CATALYST": "catalyst", "MAJOR_CATALYST": "catalyst_major"}


def clean(value):
    if isinstance(value, dict):
        return {key: clean(item) for key, item in value.items() if key != "$type"}
    if isinstance(value, list):
        return [clean(item) for item in value]
    return value


def referenced_ids(value, type_name=None):
    if isinstance(value, dict):
        if value.get("guid") and (type_name is None or value.get("TypeName") == type_name):
            return {value["guid"]}
        return set().union(*(referenced_ids(item, type_name) for item in value.values()))
    if isinstance(value, list):
        return set().union(*(referenced_ids(item, type_name) for item in value))
    return set()


def ability_ids(value):
    return referenced_ids(value, "AbStDa")


def named_stats(record):
    return {name: round(record.get(field, 0) / (100000 if field in ("def", "chc", "chm") else 1), 4)
            for name, field in STAT_FIELDS.items()}


def resource_costs(items, definition, creature_id=None):
    costs = []
    for item in items:
        resource = definition(item["rt"]["guid"])
        material = "dna" if resource.get("cr", {}).get("guid") == creature_id else MATERIALS.get(resource.get("devName"), resource.get("devName", "unrecognized"))
        costs.append({"material": material, "amount": item["a"]["v"]})
    return sorted(costs, key=lambda item: item["material"])


def rarity_rules(rarity, definition):
    levels = []
    for level in rarity["LabEvolutionTable"]:
        currency = definition(level["Currency"]["guid"])
        levels.append({"level": level["Level"], "dnaCost": level["DnaNeeded"],
                       "currency": MATERIALS.get(currency.get("devName"), currency.get("devName", "unrecognized")),
                       "currencyAmount": level["CurrencyAmount"]})
    return {"startingLevel": rarity["bLvl"], "highestTableLevel": max(level["level"] for level in levels), "levels": levels}


def creature_facts(creature_id, creature, attributes, definition):
    row = creature["rl"][0]
    facts = {
        "resistances": {field: round(attributes.get(field, 0) / 100000, 4) for field in RESISTANCES},
        "abilityLoadout": {name: sorted(referenced_ids(value)) for name, value in {
            "active": row.get("al"), "swapIn": row.get("sia"), "counter": row.get("ac"),
            "onEscape": row.get("aooe"), "reactive": row.get("rpl"), "passives": row.get("pl"),
        }.items()},
        "enhancements": [], "training": None,
    }
    prestige = creature.get("pll", {}).get("guid")
    if prestige:
        stages = definition(prestige)
        for index, reference in enumerate(stages["pll"], 1):
            stage = definition(reference["guid"])
            benefit = stage["pb"]
            facts["enhancements"].append({
                "level": index, "unlockLevel": stage["ull"], "dnaCost": stage["dca"],
                "requirements": resource_costs(stage["ulc"], definition, creature_id),
                "benefit": {"kind": benefit["$type"].split(",")[0], "data": clean(benefit)},
            })
    distribution = creature.get("capd", {}).get("guid")
    milestones = creature.get("mlnc", {}).get("guid")
    if distribution:
        settings = definition(distribution)
        training = {"caps": named_stats(definition(settings["cap"]["guid"])),
                    "perPoint": named_stats(definition(settings["app"]["guid"])), "milestones": []}
        if milestones:
            for milestone in definition(milestones)["mln"]:
                costs = resource_costs(milestone["uc"], definition, creature_id)
                training["milestones"].append({
                    "level": milestone["cl"], "costs": costs,
                    "dnaCost": sum(cost["amount"] for cost in costs if cost["material"] == "dna"),
                    "abilityIds": sorted(ability_ids(milestone["msn"])),
                    "rewards": [{"kind": reward["$type"].split(",")[0], "data": clean(reward)} for reward in milestone["msn"]],
                })
        facts["training"] = training
    return facts


def change_report(previous, current, identities):
    before = {item["id"]: item for item in previous.get("creatures", [])} if previous else {}
    after = {item["id"]: item for item in current["creatures"]}
    changes = []
    for guid in sorted(before.keys() & after.keys()):
        fields = {field: {"before": before[guid][field], "after": after[guid][field]}
                  for field in ("rarity", "stats", "moves", "resistances", "abilityLoadout", "enhancements", "training")
                  if field in before[guid] and before[guid][field] != after[guid][field]}
        if fields:
            changes.append({"id": guid, "displayName": identities.get(guid, {}).get("displayName", after[guid]["internalName"]), "fields": fields})
    return {
        "schemaVersion": "jwa-game-changes.v1", "fromGameVersion": previous.get("version") if previous else None,
        "toGameVersion": current["version"], "packageSha256": current["sha256"],
        "baseline": not previous or previous.get("extractionVersion") != EXTRACTION_VERSION,
        "addedIds": sorted(after.keys() - before.keys()) if previous else [],
        "removedIds": sorted(before.keys() - after.keys()), "changedCreatures": changes,
        "rarityChanges": {name: {"before": previous["rarityRules"][name], "after": rule}
                          for name, rule in current["rarityRules"].items()
                          if previous and name in previous.get("rarityRules", {}) and previous["rarityRules"][name] != rule},
    }


def progression_checks(game, identities, roster, details):
    by_name = {item["name"]: item for item in roster}
    mismatches = []
    checked = 0

    def compare(guid, field, packaged, paleo):
        nonlocal checked
        checked += 1
        if packaged != paleo:
            mismatches.append({"id": guid, "displayName": identities[guid]["displayName"], "field": field, "package": packaged, "paleo": paleo})

    for creature in game["creatures"]:
        guid = creature["id"]
        if guid not in identities:
            continue
        canonical = by_name.get(identities[guid]["displayName"])
        if canonical is None or canonical["uuid"] not in details:
            continue
        detail = details[canonical["uuid"]]
        if creature["enhancements"] or detail.get("enhancements"):
            packaged = [{"level": row["level"], "dnaCost": row["dnaCost"], "requirements": sorted(row["requirements"], key=lambda item: item["material"])} for row in creature["enhancements"]]
            paleo = [{"level": index, "dnaCost": row["cost"], "requirements": sorted([{"material": key, "amount": value} for key, value in row["req"]], key=lambda item: item["material"])} for index, row in enumerate(detail.get("enhancements", []), 1)]
            compare(guid, "enhancementCosts", packaged, paleo)
        training = creature["training"]
        if training is not None and detail.get("points"):
            for target, field in (("caps", "cap"), ("perPoint", "delta")):
                paleo = {key: round(value, 4) for key, value in detail["points"][field].items() if key in STAT_FIELDS}
                compare(guid, "training." + target, training[target], paleo)
            # Compare level and DNA requirements; retain raw reward records for review.
            compare(guid, "milestoneCosts", [{"level": row["level"], "dnaCost": row["dnaCost"]} for row in training["milestones"]],
                    [{"level": row["lv"], "dnaCost": row["cost"]} for row in detail.get("milestones", [])])
    return {"schemaVersion": "jwa-progression-checks.v1", "gameVersion": game["version"],
            "checkedFields": checked, "mismatches": mismatches}


def render_report(changes, checks):
    lines = [f'# Game data report: {changes["toGameVersion"]}', "",
             "This report describes packaged data. It does not change app calculations.", ""]
    if changes["baseline"]:
        lines += ["A baseline was established for newly retained fields. Missing fields in the older extraction are not treated as game changes.", ""]
    lines += [f'Changed creature records: {len(changes["changedCreatures"])}.',
              f'Package IDs added: {len(changes["addedIds"])}; removed: {len(changes["removedIds"])}.',
              f'Changed rarity tables: {len(changes["rarityChanges"])}.', "",
              "## Creature changes", ""]
    lines += [f'- {item["displayName"]}: {", ".join(item["fields"])}' for item in changes["changedCreatures"]] or ["No changes in previously retained creature fields."]
    lines += ["", "## Progression checks against Paleo", "",
              f'Fields compared: {checks["checkedFields"]}. Differences needing review: {len(checks["mismatches"])}.', "",
              "Differences can reflect package age, server-side changes, or source interpretation. They are not applied automatically.", ""]
    lines += [f'- {item["displayName"]}: {item["field"]}' for item in checks["mismatches"]] or ["No differences found in the compared fields."]
    lines += ["", "See `game-changes.json` and `progression-checks.json` for values and record IDs.", ""]
    return "\n".join(lines)
