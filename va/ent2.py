"""Sonde : quelle methode accepte l'ecriture d'entitlements ?"""
import json

import catalog
import config
import vcapi

lockfile = vcapi.Lockfile.wait()
local = vcapi.LocalAPI(lockfile)
tokens = local.entitlements()
region = local.region()
puuid, shard = tokens["subject"], region["shard"]
pd = vcapi.PdAPI(shard, tokens["access"], tokens["entitlements"], puuid,
                 vcapi.client_version(local))

skins, _ = catalog.build()
candidat = next(s for s in skins if s.levels and "standard" not in s.name.lower())
item_type = config.ITEM_TYPES["levels"]
url = f"https://pd.{shard}.a.pvp.net/store/v1/entitlements/{puuid}/{item_type}"
body = [candidat.levels[-1]]

print("cible :", candidat.name, "|", body[0])
print("GET avant ->", pd.count(item_type))

for label, method, kwargs in (
    ("put/json", "PUT", {"json": body}),
    ("put/data", "PUT", {"data": json.dumps(body),
                         "headers": {"Content-Type": "application/json"}}),
    ("post/json", "POST", {"json": {"ItemIDs": body}}),
):
    r = pd._http.request(method, url, timeout=15, **kwargs)
    print(f"\n{label} -> {r.status_code}")
    print("  url :", r.request.url)
    print("  corps :", r.text[:200])
    print("  compteur :", pd.count(item_type))