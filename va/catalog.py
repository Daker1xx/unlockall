"""Catalogue local des skins (niveaux + chromas) via valorant-api (cache disque)."""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

import requests

import config

log = logging.getLogger(__name__)
API = "https://valorant-api.com/v1"


@dataclass
class Skin:
    uuid: str
    name: str
    weapon: str
    weapon_uuid: str
    levels: list[str] = field(default_factory=list)
    chromas: list[str] = field(default_factory=list)


def build(force: bool = False) -> tuple[list[Skin], dict[str, str]]:
    if config.CATALOG_CACHE.exists() and not force:
        raw = json.loads(config.CATALOG_CACHE.read_text(encoding="utf-8"))
        return [Skin(**s) for s in raw["skins"]], raw["buddies"]

    log.info("recuperation du catalogue...")
    weapons = requests.get(f"{API}/weapons", timeout=20).json()["data"]
    buddies_raw = requests.get(f"{API}/buddies", timeout=20).json()["data"]

    skins: list[Skin] = []
    for weapon in weapons:
        for skin in weapon.get("skins", []):
            if not skin.get("displayIcon"):
                continue
            skins.append(Skin(
                uuid=skin["uuid"],
                name=skin["displayName"],
                weapon=weapon["displayName"],
                weapon_uuid=weapon["uuid"],
                levels=[lvl["uuid"] for lvl in skin.get("levels", [])],
                chromas=[c["uuid"] for c in skin.get("chromas", [])],
            ))
    buddies = {b["uuid"]: b["displayName"] for b in buddies_raw if b.get("displayIcon")}

    config.CACHE_DIR.mkdir(parents=True, exist_ok=True)
    config.CATALOG_CACHE.write_text(json.dumps({
        "skins": [s.__dict__ for s in skins],
        "buddies": buddies,
    }), encoding="utf-8")
    log.info("%d skins, %d buddies en cache", len(skins), len(buddies))
    return skins, buddies