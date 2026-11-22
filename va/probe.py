"""Sonde des formes d'URI acceptees en local + verification des 503."""
import requests

import config
import vcapi

lockfile = vcapi.Lockfile.wait()
local = vcapi.LocalAPI(lockfile)
tokens = local.entitlements()
region = local.region()
pd = vcapi.PdAPI(
    region["shard"], tokens["access"], tokens["entitlements"],
    tokens["subject"], vcapi.client_version(local),
)
p = tokens["subject"]
t = config.ITEM_TYPES["skins"]

local_paths = [
    f"/store/v1/wallet/{p}",
    f"/store/v1/entitlements/{p}",
    f"/store/v1/entitlements/{p}/{t}",
    f"/store/v1/entitlements/{p}/{t}/",
    f"/store/v1/entitlements?puuid={p}&itemTypeId={t}",
    f"/v1/entitlements/{p}/{t}",
    f"/entitlements/v1/{p}/{t}",
    f"/riotclient/store/v1/entitlements/{p}/{t}",
]

print("== client local : formes d'URI ==")
for path in local_paths:
    for method in ("GET", "PUT"):
        try:
            r = local._http.request(
                method, f"{local._base}{path}",
                json=[] if method == "PUT" else None,
                verify=False, timeout=8,
            )
            print(f"  {method:4} {path}\n       -> {r.status_code} {r.text[:140]}")
        except Exception as exc:
            print(f"  {method:4} {path}\n       -> EXC {type(exc).__name__}")

print("\n== pd : nature des 503 ==")
H = dict(pd._http.headers)
for path in (
    f"/inventory/v1/entitlements/{p}/{t}",
    f"/entitlements/v1/{p}/{t}",
):
    for method in ("GET", "PUT"):
        r = requests.request(
            method, pd._base + path,
            json=[] if method == "PUT" else None,
            headers=H, timeout=8,
        )
        ct = r.headers.get("content-type", "?")
        print(f"  {method:4} {path}\n       -> {r.status_code} [{ct}] {r.text[:180]}")