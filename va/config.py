from __future__ import annotations

import os
from pathlib import Path

APP_NAME = "valorant-unlock"
ROOT = Path(__file__).resolve().parent

LOCALAPPDATA = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
LOCKFILE_PATH = LOCALAPPDATA / "Riot Games" / "Riot Client" / "Config" / "lockfile"

CACHE_DIR = ROOT / ".cache"
CATALOG_CACHE = CACHE_DIR / "catalog.json"
WEAPONS_CACHE = CACHE_DIR / "weapons.json"
LOG_FILE = ROOT / "out.log"

LOCAL_URL = "{protocol}://127.0.0.1:{port}"
PD_URL = "https://pd.{shard}.a.pvp.net"

NIL = "00000000-0000-0000-0000-000000000000"

ITEM_TYPES = {
    "levels":  "e7c63390-eda7-46e0-bb7a-a6abdacd2433",
    "chromas": "3ad1b2b2-acdb-4524-852f-954a76ddae0a",
    "buddies": "dd3bf334-87f3-40bd-b043-682a57a8dc3a",
    "cards":   "3f296c07-64c3-494c-923b-fe692a4fa1bd",
    "sprays":  "d5f120f8-ff8c-4aac-92ea-f2b5acbe9475",
    "agents":  "01bb38e1-da47-4e6a-9b3d-945fe4655707",
    "titles":  "de7caa6b-adf7-4588-bbd1-143831e786c6",
}

REQUEST_TIMEOUT = 10
LOCKFILE_TIMEOUT = 120