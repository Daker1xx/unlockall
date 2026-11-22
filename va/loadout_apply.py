"""Ecrit les entitlements, puis le loadout, puis verifie ce qui a tenu."""
import random

import catalog
import config
import loadout
import vcapi

NIL = config.NIL


def index_par_arme(skins):
    out = {}
    for s in skins:
        if not s.levels or "standard" in s.name.lower():
            continue
        out.setdefault(s.weapon_uuid, []).append(s)
    return out


def appliquer(guns, index):
    niveaux, chromas = [], []
    for g in guns:
        opts = index.get(g.get("ID"))
        if not opts:
            continue
        skin = random.choice(opts)
        niveau = skin.levels[-1]
        g["SkinID"] = skin.uuid
        g["SkinLevelID"] = niveau
        g["ChromaID"] = skin.chromas[0] if skin.chromas else NIL
        if not isinstance(g.get("Attachments"), list):
            g["Attachments"] = []
        niveaux.append(niveau)
        if skin.chromas:
            chromas.append(skin.chromas[0])
    return niveaux, chromas


def main():
    lockfile = vcapi.Lockfile.wait()
    local = vcapi.LocalAPI(lockfile)
    tokens = local.entitlements()
    region = local.region()
    pd = vcapi.PdAPI(
        region["shard"], tokens["access"], tokens["entitlements"],
        tokens["subject"], vcapi.client_version(local),
    )

    noms = {v: k for k, v in loadout.weapon_ids().items()}

    skins, _ = catalog.build()
    index = index_par_arme(skins)
    print("skins exploitables :", sum(len(v) for v in index.values()))

    body = pd.get_loadout()
    print("version lue :", body.get("Version"))

    n1, c1 = appliquer(body.get("Guns", []), index)
    n2, c2 = appliquer(body.get("Melee") or [], index)
    niveaux, chromas = n1 + n2, c1 + c2
    print(f"a accorder : {len(niveaux)} niveaux, {len(chromas)} chromas")

    if pd.count(config.ITEM_TYPES["levels"]) == 0:
        pd.grant(config.ITEM_TYPES["levels"], niveaux)
        pd.grant(config.ITEM_TYPES["chromas"], chromas)
        relus = pd.count(config.ITEM_TYPES["levels"])
        print("niveaux relus :", relus)
        if relus == 0:
            raise SystemExit("entitlements vides -> loadout non applique")
    else:
        print("entitlements deja presents, pas de reecriture")

    pd.put_loadout(body)
    print("PUT loadout -> ok")

    apres = pd.get_loadout()
    recu = {g["ID"]: g for g in apres.get("Guns", [])}

    tenu = mauvais_niveau = perdu = 0
    for g in body.get("Guns", []):
        r = recu.get(g["ID"])
        if not r:
            continue
        if r.get("SkinID") != g["SkinID"]:
            perdu += 1
            print(f"  REVERT {noms.get(g['ID'], g['ID'])}")
            continue
        tenu += 1
        if r.get("SkinLevelID") in (r.get("SkinID"), NIL):
            mauvais_niveau += 1

    print(f"\nskins persistes : {tenu} | reverties : {perdu} | niveau invalide : {mauvais_niveau}")
    print("version apres :", apres.get("Version"))


if __name__ == "__main__":
    main()