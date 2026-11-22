"""Point d'entrée : connexion, sélection, application au loadout (ou entitlements)."""
from __future__ import annotations

import argparse
import logging
import sys

import catalog
import config
import loadout
import vcapi

log = logging.getLogger("unlock")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Équip de skins Valorant via le loadout (non persistant)")
    p.add_argument("--all", action="store_true", help="tout sélectionner")
    p.add_argument("--filter", default=None, help="filtre sur le nom de skin")
    p.add_argument("--weapon", default=None, help="filtre sur le nom d'arme")
    p.add_argument("--limit", type=int, default=None, help="nombre max de skins")
    p.add_argument("--with-buddies", action="store_true", help="inclure les buddies (entitlements uniquement)")
    p.add_argument("--entitlements", action="store_true", help="tenter l'écriture d'entitlements au lieu du loadout")
    p.add_argument("--remove", action="store_true", help="retirer au lieu d'ajouter (entitlements)")
    p.add_argument("--dry-run", action="store_true", help="afficher sans écrire")
    p.add_argument("--refresh-catalog", action="store_true")
    return p.parse_args()


def select(skins: list, args: argparse.Namespace) -> list:
    out = skins
    if args.filter:
        needle = args.filter.lower()
        out = [s for s in out if needle in s.name.lower()]
    if args.weapon:
        needle = args.weapon.lower()
        out = [s for s in out if needle in s.weapon.lower()]
    if args.limit:
        out = out[: args.limit]
    return out


def main() -> int:
    args = parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(message)s",
        handlers=[logging.StreamHandler(sys.stdout), logging.FileHandler(config.LOG_FILE, "a", "utf-8")],
    )

    log.info("attente du Riot Client…")
    lockfile = vcapi.Lockfile.wait()
    local = vcapi.LocalAPI(lockfile)
    tokens = local.entitlements()
    region = local.region()

    pd = vcapi.PdAPI(
        shard=region["shard"],
        access_token=tokens["access"],
        entitlements_token=tokens["entitlements"],
        puuid=tokens["subject"],
        client_version=vcapi.client_version(local),
    )

    skins, buddies = catalog.build(force=args.refresh_catalog)
    chosen = skins if args.all else select(skins, args)
    if not chosen:
        log.warning("aucun skin ne correspond aux filtres")
        return 1

    if args.dry_run:
        log.info("%d skins sélectionnés", len(chosen))
        for skin in chosen[:20]:
            print(f"  {skin.weapon} — {skin.name}")
        return 0

    if args.entitlements:
        payload = {
            "skins": [s.uuid for s in chosen],
            "levels": [lvl for s in chosen for lvl in s.levels],
            "chromas": [c for s in chosen for c in s.chromas],
        }
        if args.with_buddies:
            payload["buddies"] = list(buddies)
        log.info(
            "%d skins / %d niveaux / %d chromas — %s",
            len(payload["skins"]), len(payload["levels"]), len(payload["chromas"]),
            "suppression" if args.remove else "ajout",
        )
        for name, ids in payload.items():
            if not ids:
                continue
            pd.write_entitlements(config.ITEM_TYPES[name], ids, remove=args.remove)
            log.info("  %s : %d items écrits", name, len(ids))
        log.info("terminé — relance Valorant pour voir les skins")
        return 0

    log.info("%d skins sélectionnés — application au loadout", len(chosen))
    body, applied = loadout.build(pd.get_loadout(), chosen)
    if not applied:
        log.warning("aucun skin ne correspond à une arme du loadout")
        return 1
    result = pd.put_loadout(body)
    log.info("loadout écrit, version %s", result.get("Version"))
    for line in applied:
        print(f"  {line}")
    log.info("terminé — ferme Valorant puis relance-le pour voir les skins équipés")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())