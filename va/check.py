"""Audit lecture seule : entitlements + etat du loadout."""
import config
import loadout
import vcapi

NIL = config.NIL


def main() -> int:
    lockfile = vcapi.Lockfile.wait()
    local = vcapi.LocalAPI(lockfile)
    tokens = local.entitlements()
    region = local.region()
    pd = vcapi.PdAPI(
        region["shard"], tokens["access"], tokens["entitlements"],
        tokens["subject"], vcapi.client_version(local),
    )

    print("entitlements niveaux :", pd.count(config.ITEM_TYPES["levels"]))
    print("entitlements chromas :", pd.count(config.ITEM_TYPES["chromas"]))

    data = pd.get_loadout()
    noms = {v: k for k, v in loadout.weapon_ids().items()}
    print("version serveur :", data.get("Version"))

    armes = list(data.get("Guns", [])) + list(data.get("Melee") or [])
    posees = invalides = 0

    for gun in armes:
        if gun.get("SkinID") in (None, NIL):
            continue
        posees += 1
        label = noms.get(gun["ID"], gun["ID"])
        niveau = gun.get("SkinLevelID")
        chroma = gun.get("ChromaID")
        flag = ""
        if niveau in (gun.get("SkinID"), None, NIL):
            invalides += 1
            flag = "  <-- niveau invalide"
        print(f"  {label:12} SkinID={gun['SkinID']} Level={niveau} Chroma={chroma}{flag}")

    print(f"\n{posees} arme(s) avec un skin non nul | {invalides} niveau(x) invalide(s)")
    if posees == 0:
        print("=> loadout ecrase, rien ne tient")
    elif invalides:
        print("=> le client ne peut pas resoudre les niveaux : skins nus")
    else:
        print("=> etat coherent cote serveur")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())