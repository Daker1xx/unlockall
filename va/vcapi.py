"""Transport bas niveau : lockfile local, auth, session, endpoints PD."""
from __future__ import annotations

import base64
import json
import logging
import time
from dataclasses import dataclass
from typing import Any

import requests
import urllib3
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

import config

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
log = logging.getLogger(__name__)

CLIENT_PLATFORM = base64.b64encode(json.dumps({
    "platformType": "PC",
    "platformOS": "Windows",
    "platformOSVersion": "10.0.19042.1.256.64bit",
    "platformChipset": "Unknown",
}).encode()).decode()

SHARD_BY_REGION = {
    "na": "na", "br": "na", "latam": "na", "lan": "na", "las": "na",
    "eu": "eu", "tr": "eu", "ru": "eu", "eune": "eu", "euw": "eu",
    "ap": "ap", "jp": "ap", "oce": "ap", "sea": "ap",
    "kr": "kr",
}


class RiotError(RuntimeError):
    pass


def _first_jwt(*values: Any) -> str | None:
    for value in values:
        if isinstance(value, str) and value.count(".") == 2 and len(value) > 40:
            return value
    return None


@dataclass(frozen=True)
class Lockfile:
    pid: int
    port: int
    password: str
    protocol: str

    @classmethod
    def read(cls) -> "Lockfile":
        try:
            raw = config.LOCKFILE_PATH.read_text(encoding="utf-8").strip()
        except FileNotFoundError as exc:
            raise RiotError(f"lockfile introuvable : {config.LOCKFILE_PATH}") from exc
        parts = raw.split(":")
        if len(parts) != 5:
            raise RiotError(f"lockfile illisible : {raw!r}")
        _, pid, port, password, protocol = parts
        return cls(int(pid), int(port), password, protocol)

    @classmethod
    def wait(cls, timeout: float = config.LOCKFILE_TIMEOUT) -> "Lockfile":
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                return cls.read()
            except RiotError:
                time.sleep(1.0)
        raise RiotError("Riot Client n'a pas demarre dans le delai imparti")


class LocalAPI:
    """API locale du Riot Client (port ephemere, Basic auth)."""

    def __init__(self, lockfile: Lockfile) -> None:
        self._base = config.LOCAL_URL.format(protocol=lockfile.protocol, port=lockfile.port)
        self._http = _session()
        token = base64.b64encode(f"riot:{lockfile.password}".encode()).decode()
        self._http.headers["Authorization"] = f"Basic {token}"
        self._cache: dict[str, Any] = {}

    def get(self, path: str) -> Any:
        if path in self._cache:
            return self._cache[path]
        resp = self._http.get(f"{self._base}{path}", timeout=config.REQUEST_TIMEOUT, verify=False)
        resp.raise_for_status()
        self._cache[path] = resp.json()
        return self._cache[path]

    def entitlements(self, timeout: float = 20.0) -> dict:
        deadline = time.monotonic() + timeout
        data: dict[str, Any] = {}
        while True:
            self._cache.pop("/entitlements/v1/token", None)
            data = self.get("/entitlements/v1/token")
            access = _first_jwt(data.get("accessToken"))
            ent = _first_jwt(data.get("token"), data.get("entitlements_token"), data.get("entitlements"))
            subject = data.get("subject")
            if access and ent and subject:
                return {"entitlements": ent, "access": access, "subject": subject}
            if time.monotonic() >= deadline:
                break
            time.sleep(1.0)
        detail = ", ".join(f"{k}={str(v)[:24]!r}" for k, v in data.items())
        raise RiotError(f"reponse d'entitlements incomplete : {detail}")

    def region(self) -> dict:
        for path in ("/riotclient/region-locale", "/riotclient/get_region_locale"):
            try:
                data = self.get(path)
            except requests.HTTPError:
                continue
            shard = data.get("shard") or SHARD_BY_REGION.get((data.get("region") or "").lower())
            if shard:
                return {**data, "shard": shard}
        raise RiotError("impossible de resoudre la region")


class PdAPI:
    """API PD de Valorant (HTTPS public, JWT d'entitlements)."""

    def __init__(self, shard: str, access_token: str, entitlements_token: str, puuid: str, client_version: str) -> None:
        self._base = config.PD_URL.format(shard=shard)
        self._http = _session()
        self._http.headers.update({
            "Authorization": f"Bearer {access_token}",
            "X-Riot-Entitlements-JWT": entitlements_token,
            "X-Riot-ClientPlatform": CLIENT_PLATFORM,
            "X-Riot-ClientVersion": client_version,
        })
        self._puuid = puuid

    # --- loadout -------------------------------------------------------
    def get_loadout(self) -> dict:
        url = f"{self._base}/personalization/v3/players/{self._puuid}/playerloadout"
        resp = self._http.get(url, timeout=config.REQUEST_TIMEOUT)
        resp.raise_for_status()
        return resp.json()

    def put_loadout(self, body: dict) -> dict:
        url = f"{self._base}/personalization/v3/players/{self._puuid}/playerloadout"
        resp = self._http.put(url, json=body, timeout=config.REQUEST_TIMEOUT)
        if resp.status_code >= 400:
            log.error("HTTP %s : %s", resp.status_code, resp.text[:300])
        resp.raise_for_status()
        return resp.json()

    # --- entitlements --------------------------------------------------
    def _ent_url(self, item_type: str) -> str:
        return f"{self._base}/store/v1/entitlements/{self._puuid}/{item_type}"

    def grant(self, item_type: str, item_ids: list[str], chunk: int = 100) -> None:
        url = self._ent_url(item_type)
        for start in range(0, len(item_ids), chunk):
            batch = item_ids[start:start + chunk]
            resp = self._http.put(url, json=batch, timeout=config.REQUEST_TIMEOUT)
            print(f"  PUT {resp.request.url} -> {resp.status_code}")
            if resp.status_code >= 400:
                print("  ", resp.text[:200])
                resp.raise_for_status()
            time.sleep(0.2)

    def revoke(self, item_type: str, item_ids: list[str], chunk: int = 100) -> None:
        url = self._ent_url(item_type)
        for start in range(0, len(item_ids), chunk):
            batch = item_ids[start:start + chunk]
            resp = self._http.delete(url, json=batch, timeout=config.REQUEST_TIMEOUT)
            if resp.status_code >= 400:
                print("  ", resp.text[:200])
                resp.raise_for_status()
            time.sleep(0.2)

    def count(self, item_type: str) -> int:
        resp = self._http.get(self._ent_url(item_type), timeout=config.REQUEST_TIMEOUT)
        resp.raise_for_status()
        return len(resp.json().get("Entitlements") or [])


def client_version(local: LocalAPI) -> str:
    try:
        for entry in local.get("/product-session/v1/external-sessions").values():
            if entry.get("productId") == "valorant" and entry.get("version"):
                return entry["version"]
    except requests.HTTPError:
        pass
    data = requests.get("https://valorant-api.com/v1/version", timeout=10).json()
    return data["data"]["riotClientVersion"]


def _session() -> requests.Session:
    http = requests.Session()
    retry = Retry(
        total=3,
        backoff_factor=0.4,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=None,
    )
    adapter = HTTPAdapter(max_retries=retry)
    http.mount("https://", adapter)
    http.mount("http://", adapter)
    return http