"""IDs d'armes, resolus depuis valorant-api (cle = nom en minuscules)."""
from __future__ import annotations

import json

import requests

import config

API = "https://valorant-api.com/v1/weapons"


def weapon_ids(force: bool = False) -> dict[str, str]:
    if config.WEAPONS_CACHE.exists() and not force:
        return json.loads(config.WEAPONS_CACHE.read_text(encoding="utf-8"))

    data = requests.get(API, timeout=20).json()["data"]
    ids = {w["displayName"].lower(): w["uuid"] for w in data}

    config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    config.WEAPONS_CACHE.write_text(json.dumps(ids, indent=2), encoding="utf-8")
    return ids