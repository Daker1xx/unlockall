#!/usr/bin/env python3
"""
Changer les skins d'arme via l'API du client — un seul fichier, menu.

RÉALITÉ : le loadout n'« unlock » rien. Il applique une skin que le compte
possède déjà ; le client résout via les entitlements, sans entitlement c'est
standard. Les écritures d'entitlements sont réputées fermées (405) — --probe
le vérifie pour de vrai.

Hypothèse à tester : le blocage est peut-être PAR ARME et non par skin. Voir
le README de conversation : une skin gratuite de contrat d'agent sur un
pistolet, puis tentative d'une autre skin du même pistolet.

Usage :
    python unlock.py                     # menu
    python unlock.py --check             # équipé + possédé ou non
    python unlock.py --list              # armes et ce que tu possèdes
    python unlock.py --list vandal --tout
    python unlock.py --weapon vandal     # choisir (possédées d'abord)
    python unlock.py --random            # possédées seulement
    python unlock.py --probe             # les écritures d'entitlements, pour de vrai
    python unlock.py --raw vandal
    python unlock.py --restore
"""
from __future__ import annotations

import base64
import json
import os
import random
import subprocess
import sys
import time
from pathlib import Path

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

ROOT = Path(__file__).resolve().parent
CACHE_DIR = ROOT / "cache"
CACHE_FILE = CACHE_DIR / "skins.json"
BACKUP_FILE = ROOT / "loadout_backup.json"

LOCALAPPDATA = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
LOCKFILE = LOCALAPPDATA / "Riot Games" / "Riot Client" / "Config" / "lockfile"
RIOT_CLIENT_EXE = [
    Path(r"C:\Riot Games\Riot Client\RiotClientServices.exe"),
    Path(r"C:\Program Files\Riot Games\Riot Client\RiotClientServices.exe"),
]

API = "https://valorant-api.com/v1"
NIL = "00000000-0000-0000-0000-000000000000"
CACHE_VERSION = 2

TYPE_SKINS = "bcef87d6-209b-46c6-8b19-fbe40bd95abc"
TYPE_NIVEAUX = "e7c63390-eda7-46e0-bb7a-a6abdacd2433"
TYPE_CHROMAS = "3ad1b2b2-acdb-4524-852f-954a76ddae0a"

SHARD_BY_REGION = {
    "na": "na", "br": "na", "latam": "na", "lan": "na", "las": "na",
    "eu": "eu", "tr": "eu", "ru": "eu", "eune": "eu", "euw": "eu",
    "ap": "ap", "jp": "ap", "oce": "ap", "sea": "ap",
    "kr": "kr",
}

CLIENT_PLATFORM = base64.b64encode(json.dumps({
    "platformType": "PC",
    "platformOS": "Windows",
    "platformOSVersion": "10.0.19042.1.256.64bit",
    "platformChipset": "Unknown",
}).encode()).decode()

PAR_ARME: dict[str, list[dict]] = {}
UUID_ARME: dict[str, str] = {}
NOMS: dict[str, str] = {}
POSSEDE: dict[str, set[str]] = {}


class RiotError(RuntimeError):
    pass


def say(msg: str = "") -> None:
    print(msg, flush=True)


def jeu_ouvert() -> bool:
    try:
        sortie = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq VALORANT.exe", "/NH"],
            capture_output=True, timeout=10,
        ).stdout or b""
        return b"valorant.exe" in sortie.lower()
    except (OSError, subprocess.SubprocessError):
        return False


def lire_lockfile() -> tuple[int, str, str]:
    brut = LOCKFILE.read_text(encoding="utf-8").strip()
    parts = brut.split(":")
    if len(parts) != 5:
        raise RiotError(f"lockfile illisible : {brut!r}")
    _, _pid, port, motdepasse, protocole = parts
    return int(port), motdepasse, protocole


def demarrer_client() -> None:
    for exe in RIOT_CLIENT_EXE:
        if exe.exists():
            say(f"  lancement de {exe}")
            subprocess.Popen([str(exe), "--launch-product=valorant", "--launch-patchline=live"])
            return
    say("  Riot Client introuvable, ouvre-le à la main")


def attendre_lockfile(timeout: float = 120.0) -> tuple[int, str, str]:
    limite = time.monotonic() + timeout
    annonce = False
    while time.monotonic() < limite:
        if LOCKFILE.exists():
            try:
                return lire_lockfile()
            except (RiotError, ValueError):
                pass
        if not annonce:
            say("En attente du Riot Client…")
            if not LOCKFILE.exists():
                demarrer_client()
            annonce = True
        time.sleep(1.0)
    raise RiotError("Riot Client absent : ouvre-le puis relance l'outil")


class Ctx:
    def __init__(self) -> None:
        self.http = requests.Session()
        self._inv: dict[str, set[str]] | None = None
        self._inv_at = 0.0
        self.refresh()

    def refresh(self) -> None:
        port, motdepasse, protocole = attendre_lockfile()
        base = f"{protocole}://127.0.0.1:{port}"
        jeton = base64.b64encode(f"riot:{motdepasse}".encode()).decode()
        entetes_locaux = {"Authorization": f"Basic {jeton}"}

        ent = self._get(f"{base}/entitlements/v1/token", entetes_locaux)
        self.access = ent["accessToken"]
        self.ent_token = ent["token"]
        self.puuid = ent["subject"]

        region = self._get(f"{base}/riotclient/region-locale", entetes_locaux)
        shard = region.get("shard") or SHARD_BY_REGION.get((region.get("region") or "").lower())
        if not shard:
            raise RiotError("région impossible à résoudre")
        self.pd = f"https://pd.{shard}.a.pvp.net"

        self.entetes = {
            "Authorization": f"Bearer {self.access}",
            "X-Riot-Entitlements-JWT": self.ent_token,
            "X-Riot-ClientPlatform": CLIENT_PLATFORM,
            "X-Riot-ClientVersion": self._version_client(base, entetes_locaux),
            "Content-Type": "application/json",
        }
        self._inv = None

    @staticmethod
    def _get(url: str, entetes: dict, essais: int = 8) -> dict:
        derniere = None
        for _ in range(essais):
            r = requests.get(url, headers=entetes, verify=False, timeout=10)
            if r.status_code < 400:
                return r.json()
            derniere = r
            time.sleep(1.0)
        raise RiotError(f"{url} -> {derniere.status_code} {derniere.text[:120]}")

    def _version_client(self, base: str, entetes: dict) -> str:
        try:
            sessions = self._get(f"{base}/product-session/v1/external-sessions", entetes)
            for entree in sessions.values():
                if entree.get("productId") == "valorant" and entree.get("version"):
                    return entree["version"]
        except RiotError:
            pass
        data = requests.get(f"{API}/version", timeout=10).json()
        return data["data"]["riotClientVersion"]

    def _url(self) -> str:
        return f"{self.pd}/personalization/v3/players/{self.puuid}/playerloadout"

    def lire(self) -> dict:
        r = self.http.get(self._url(), headers=self.entetes, timeout=20)
        if r.status_code in (401, 403):
            self.refresh()
            return self.lire()
        r.raise_for_status()
        return r.json()

    def ecrire(self, corps: dict) -> None:
        r = self.http.put(self._url(), headers=self.entetes, json=corps, timeout=20)
        if r.status_code in (401, 403):
            self.refresh()
            return self.ecrire(corps)
        if r.status_code >= 400:
            raise RiotError(f"PUT refusé -> {r.status_code} {r.text[:200]}")

    def inventaire(self, max_age: float = 60.0) -> dict[str, set[str]]:
        if self._inv is not None and time.monotonic() - self._inv_at < max_age:
            return self._inv
        out: dict[str, set[str]] = {TYPE_SKINS: set(), TYPE_NIVEAUX: set(), TYPE_CHROMAS: set()}
        try:
            r = self.http.get(f"{self.pd}/store/v1/entitlements/{self.puuid}",
                              headers=self.entetes, timeout=30)
            if r.status_code < 400:
                for bloc in (r.json() or {}).get("EntitlementsByTypes") or []:
                    tid = bloc.get("ItemTypeID")
                    if tid in out:
                        out[tid] = {e.get("ItemID") for e in bloc.get("Entitlements") or []}
        except (requests.RequestException, ValueError):
            pass
        self._inv, self._inv_at = out, time.monotonic()
        return out


def possede(skin: dict) -> bool:
    if not POSSEDE:
        return False
    if skin["uuid"] in POSSEDE.get(TYPE_SKINS, set()):
        return True
    return any(n["uuid"] in POSSEDE.get(TYPE_NIVEAUX, set()) for n in skin["niveaux"])


def construire(force: bool = False) -> None:
    if CACHE_FILE.exists() and not force:
        brut = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        if brut.get("version") == CACHE_VERSION:
            UUID_ARME.update(brut["uuid_armes"])
            for arme, skins in brut["armes"].items():
                PAR_ARME[arme] = skins
                for s in skins:
                    NOMS[s["uuid"]] = s["nom"]
            return

    say("Récupération du catalogue…")
    armes: dict[str, list[dict]] = {}
    uuid_armes: dict[str, str] = {}
    for arme in requests.get(f"{API}/weapons", timeout=25).json()["data"]:
        uuid_armes[arme["uuid"]] = arme["displayName"]
        skins = []
        for s in arme.get("skins", []):
            if not s.get("levels") or "standard" in s["displayName"].lower():
                continue
            skins.append({
                "uuid": s["uuid"],
                "nom": s["displayName"],
                "niveaux": [{
                    "uuid": n["uuid"],
                    "nom": n.get("displayName") or f"Niveau {i + 1}",
                    "chromas": [c["uuid"] for c in n.get("chromas", [])],
                } for i, n in enumerate(s["levels"])],
                "chromas": [{"uuid": c["uuid"], "nom": c.get("displayName") or "Base"}
                            for c in s.get("chromas", [])],
            })
        if skins:
            armes[arme["displayName"]] = sorted(skins, key=lambda x: x["nom"].lower())

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps(
        {"version": CACHE_VERSION, "armes": armes, "uuid_armes": uuid_armes},
        ensure_ascii=False), encoding="utf-8")

    PAR_ARME.clear(); UUID_ARME.clear(); NOMS.clear()
    UUID_ARME.update(uuid_armes)
    for arme, skins in armes.items():
        PAR_ARME[arme] = skins
        for s in skins:
            NOMS[s["uuid"]] = s["nom"]
    say(f"  {sum(len(v) for v in armes.values())} skins sur {len(armes)} armes")


def resoudre_arme(nom: str) -> str | None:
    nom = nom.strip().lower()
    for arme in PAR_ARME:
        if arme.lower() == nom:
            return arme
    for arme in PAR_ARME:
        if nom in arme.lower():
            return arme
    return None


def slots(body: dict) -> list[tuple[dict, str]]:
    paires = [(g, UUID_ARME.get(g.get("ID"), g.get("ID") or "")) for g in (body.get("Guns") or [])]
    melee = body.get("Melee")
    if isinstance(melee, dict):
        paires.append((melee, "Melee"))
    return paires


def slot_de(body: dict, arme: str) -> dict | None:
    return next((slot for slot, nom in slots(body) if nom.lower() == arme.lower()), None)


def sauvegarder_une_fois(ctx: Ctx) -> None:
    if BACKUP_FILE.exists():
        return
    BACKUP_FILE.write_text(json.dumps(ctx.lire(), indent=2), encoding="utf-8")
    say(f"  loadout d'origine sauvegardé dans {BACKUP_FILE.name}")


def niveau_par_index(skin: dict, index: str | None) -> str:
    niveaux = skin["niveaux"]
    if not index or index.lower() in ("max", "dernier"):
        return niveaux[-1]["uuid"]
    try:
        i = int(index)
    except ValueError:
        return niveaux[-1]["uuid"]
    return niveaux[max(1, min(i, len(niveaux))) - 1]["uuid"]


def chroma_par_index(skin: dict, niveau_uuid: str, index: str | None) -> str:
    if index and index.lower() in ("nil", "aucun", "0"):
        return NIL
    niveau = next(n for n in skin["niveaux"] if n["uuid"] == niveau_uuid)
    compatibles = [c for c in skin["chromas"] if c["uuid"] in niveau["chromas"]]
    if not compatibles:
        return NIL
    if not index:
        return compatibles[0]["uuid"]
    try:
        i = int(index)
    except ValueError:
        return compatibles[0]["uuid"]
    return compatibles[max(1, min(i, len(compatibles))) - 1]["uuid"]


def choisir_niveau(skin: dict) -> str:
    niveaux = skin["niveaux"]
    if len(niveaux) == 1:
        return niveaux[0]["uuid"]
    say("\nNiveaux :")
    for i, n in enumerate(niveaux, 1):
        marque = "" if n["uuid"] in POSSEDE.get(TYPE_NIVEAUX, set()) else "  (non possédé)"
        say(f"  {i}. {n['nom']}{marque}")
    try:
        choix = input(f"Niveau (Entrée = {len(niveaux)}) > ").strip()
    except EOFError:
        choix = ""
    return niveau_par_index(skin, choix or "max")


def choisir_chroma(skin: dict, niveau_uuid: str) -> str:
    niveau = next(n for n in skin["niveaux"] if n["uuid"] == niveau_uuid)
    compatibles = [c for c in skin["chromas"] if c["uuid"] in niveau["chromas"]]
    if len(compatibles) <= 1:
        return compatibles[0]["uuid"] if compatibles else NIL
    say("\nChromas :")
    for i, c in enumerate(compatibles, 1):
        say(f"  {i}. {c['nom']}")
    try:
        choix = input("Chroma (Entrée = 1) > ").strip()
    except EOFError:
        choix = ""
    return chroma_par_index(skin, niveau_uuid, choix or "1")


def appliquer(ctx: Ctx, arme: str, skin: dict, niveau: str | None = None,
              chroma: str | None = None) -> None:
    niveau = niveau or choisir_niveau(skin)
    chroma = chroma or choisir_chroma(skin, niveau)

    body = ctx.lire()
    cible = slot_de(body, arme)
    if cible is None:
        say(f"  {arme} absente de ton loadout")
        return

    cible["SkinID"] = skin["uuid"]
    cible["SkinLevelID"] = niveau
    cible["ChromaID"] = chroma
    if not isinstance(cible.get("Attachments"), list):
        cible["Attachments"] = []

    sauvegarder_une_fois(ctx)
    ctx.ecrire(body)

    apres = slot_de(ctx.lire(), arme)
    if apres is None:
        say("  relecture impossible")
        return
    if apres.get("SkinID") != skin["uuid"]:
        say(f"  {arme} -> refusé (le serveur a remis {NOMS.get(apres.get('SkinID'), 'standard')})")
        return
    say(f"  {arme} -> {skin['nom']} — écrit et relu")
    if not possede(skin):
        say("  skin non possédée : le rendu restera standard.")


def menu_arme(ctx: Ctx) -> None:
    armes = sorted(PAR_ARME)
    for i, arme in enumerate(armes, 1):
        possedees = sum(1 for s in PAR_ARME[arme] if possede(s))
        say(f"  {i:2}. {arme} ({len(PAR_ARME[arme])} au catalogue, {possedees} possédées)")
    try:
        idx = int(input("\nArme > ")) - 1
    except (ValueError, EOFError):
        return
    if not 0 <= idx < len(armes):
        return
    choisir_sur_arme(ctx, armes[idx])


def choisir_sur_arme(ctx: Ctx, arme: str, filtre: str | None = None,
                     tout: bool = False) -> None:
    skins = PAR_ARME.get(arme) or []
    if not tout:
        skins = [s for s in skins if possede(s)]
        if not skins:
            say(f"  aucune skin possédée sur {arme} — --tout pour voir le catalogue")
            return
    if filtre:
        skins = [s for s in skins if filtre.lower() in s["nom"].lower()]
    if not skins:
        say("  aucun résultat")
        return
    for i, s in enumerate(skins[:60], 1):
        say(f"  {i:3}. {s['nom']}")
    if len(skins) > 60:
        say(f"  … {len(skins) - 60} de plus, affine")
    try:
        choix = int(input("Skin > ")) - 1
    except (ValueError, EOFError):
        return
    if not 0 <= choix < min(len(skins), 60):
        return
    appliquer(ctx, arme, skins[choix])


def aleatoire(ctx: Ctx) -> None:
    body = ctx.lire()
    armees = [nom for _slot, nom in slots(body) if nom in PAR_ARME]
    if not armees:
        say("  aucune arme connue dans le loadout")
        return
    sauvegarder_une_fois(ctx)
    faits = ignores = 0
    for arme in armees:
        utilisables = [s for s in PAR_ARME[arme] if possede(s)]
        if not utilisables:
            ignores += 1
            continue
        skin = random.choice(utilisables)
        niveaux = [n for n in skin["niveaux"] if n["uuid"] in POSSEDE.get(TYPE_NIVEAUX, set())]
        niveau = (niveaux[-1] if niveaux else skin["niveaux"][-1])["uuid"]
        chroma = next((c["uuid"] for c in skin["chromas"]
                       if c["uuid"] in POSSEDE.get(TYPE_CHROMAS, set())), NIL)
        body = ctx.lire()
        cible = slot_de(body, arme)
        if cible is None:
            continue
        cible["SkinID"] = skin["uuid"]
        cible["SkinLevelID"] = niveau
        cible["ChromaID"] = chroma
        if not isinstance(cible.get("Attachments"), list):
            cible["Attachments"] = []
        ctx.ecrire(body)
        faits += 1
        time.sleep(0.2)
    say(f"  {faits} arme(s) équipée(s) avec une skin possédée")
    if ignores:
        say(f"  {ignores} arme(s) ignorée(s) : aucune skin possédée dessus")


def verifier(ctx: Ctx) -> None:
    body = ctx.lire()
    say(f"version {body.get('Version')} | jeu ouvert : {'oui' if jeu_ouvert() else 'non'}")
    say(f"inventaire : {len(POSSEDE.get(TYPE_SKINS, set()))} skin(s), "
        f"{len(POSSEDE.get(TYPE_NIVEAUX, set()))} niveau(x) possédés")
    if not POSSEDE.get(TYPE_NIVEAUX):
        say("  Compte sans aucune skin : le loadout seul ne peut rien afficher.")
    ecart = 0
    for slot, arme in slots(body):
        skin_id = slot.get("SkinID")
        if skin_id in (None, NIL):
            continue
        skin = next((s for arme2 in PAR_ARME for s in PAR_ARME[arme2]
                     if s["uuid"] == skin_id), None)
        if skin is None:
            continue
        ok = possede(skin)
        if not ok:
            ecart += 1
        say(f"  {arme:14} {skin['nom'][:40]:42} {'possédée' if ok else 'NON possédée'}")
    if ecart:
        say(f"{ecart} skin(s) équipée(s) mais non possédée(s) : rendu standard.")


def sonde(ctx: Ctx) -> None:
    """Vérifie pour de vrai si les écritures d'entitlements sont ouvertes."""
    arme = next(iter(PAR_ARME))
    niveau = PAR_ARME[arme][0]["niveaux"][0]["uuid"]
    base = f"{ctx.pd}/store/v1/entitlements/{ctx.puuid}"
    essais = [
        (f"{base}/{TYPE_NIVEAUX}", {"ItemIDs": [niveau]}),
        (base, {"ItemTypeID": TYPE_NIVEAUX, "ItemIDs": [niveau]}),
        (f"{base}", [niveau]),
    ]
    for url, corps in essais:
        for methode in ("PUT", "POST", "PATCH"):
            r = ctx.http.request(methode, url, headers=ctx.entetes, json=corps, timeout=15)
            chemin = url.split("/store")[1]
            say(f"  {methode:5} {chemin} -> {r.status_code} {r.text[:70]}")


def brut(ctx: Ctx, arme: str) -> None:
    cible = slot_de(ctx.lire(), arme)
    say(json.dumps(cible, indent=2) if cible else f"  {arme} absente du loadout")


def restaurer(ctx: Ctx) -> None:
    if not BACKUP_FILE.exists():
        say("  aucune sauvegarde")
        return
    ctx.ecrire(json.loads(BACKUP_FILE.read_text(encoding="utf-8")))
    say("  loadout d'origine restauré")


def menu(ctx: Ctx) -> None:
    while True:
        say("""
  1. Choisir une arme puis une skin possédée
  2. Skin aléatoire sur les armes où tu possèdes quelque chose
  3. Vérifier l'équipé et ce qui est possédé
  4. Sonde des écritures d'entitlements
  5. Restaurer le loadout d'origine
  0. Quitter""")
        choix = input("> ").strip().lower()
        try:
            if choix == "1":
                menu_arme(ctx)
            elif choix == "2":
                aleatoire(ctx)
            elif choix == "3":
                verifier(ctx)
            elif choix == "4":
                sonde(ctx)
            elif choix == "5":
                restaurer(ctx)
            elif choix == "0":
                return
        except RiotError as exc:
            say(f"erreur : {exc}")
        except requests.RequestException as exc:
            say(f"réseau : {exc}")


def arg_apres(drapeau: str) -> str | None:
    if drapeau not in sys.argv:
        return None
    pos = sys.argv.index(drapeau)
    if len(sys.argv) > pos + 1 and not sys.argv[pos + 1].startswith("--"):
        return sys.argv[pos + 1]
    return None


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    construire(force="--refresh" in sys.argv)
    tout = "--tout" in sys.argv

    say("Connexion au Riot Client…")
    ctx = Ctx()
    POSSEDE.update(ctx.inventaire())
    say(f"  connecté ({ctx.pd.split('//')[1].split('.')[1]})")

    if "--list" in sys.argv:
        demande = arg_apres("--list")
        if not demande:
            for arme in sorted(PAR_ARME):
                possedees = sum(1 for s in PAR_ARME[arme] if possede(s))
                say(f"  {arme:14} {possedees}/{len(PAR_ARME[arme])} possédées")
            return 0
        arme = resoudre_arme(demande)
        if not arme:
            say(f"  arme inconnue : {demande}")
            return 1
        for s in PAR_ARME[arme]:
            if tout or possede(s):
                say(f"  {s['nom']}{'' if possede(s) else '  (non possédée)'}")
        return 0

    if "--raw" in sys.argv:
        cible = resoudre_arme(arg_apres("--raw") or "")
        if not cible:
            say("  arme inconnue")
            return 1
        brut(ctx, cible)
        return 0
    if "--check" in sys.argv:
        verifier(ctx)
        return 0
    if "--probe" in sys.argv:
        sonde(ctx)
        return 0
    if "--restore" in sys.argv:
        restaurer(ctx)
        return 0
    if "--random" in sys.argv:
        aleatoire(ctx)
        return 0

    demande = arg_apres("--weapon")
    if demande:
        arme = resoudre_arme(demande)
        if not arme:
            say(f"  arme inconnue : {demande}")
            return 1
        choisir_sur_arme(ctx, arme, arg_apres("--skin"), tout)
        return 0

    menu(ctx)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RiotError as exc:
        say(f"\n{exc}")
        raise SystemExit(1)
    except KeyboardInterrupt:
        say("\ninterrompu")