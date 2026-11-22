#!/usr/bin/env python3
"""
collection.py — remplit l'inventaire du client avec tout le catalogue.

La collection ne lit pas le loadout : elle lit la map d'entitlements construite
en RAM après le login (type -> tableau d'items). On localise ces tableaux par
leur ItemTypeID, on valide l'en-tête TArray {ptr, count, capacity}, puis on
écrase les entrées et on remonte le compteur. Si la capacité est atteinte, on
alloue un tampon dans le processus et on redirige le pointeur.

Le jeu est lancé automatiquement si besoin. Un chien de garde réapplique toutes
les 2 s, parce que le client reconstruit ces tableaux (changement de carte,
retour au menu, ouverture de boutique).

Usage :
    python collection.py                  # skins + niveaux + chromas
    python collection.py --types all      # + buddies, cartes, titres
    python collection.py --attach         # jeu déjà lancé
    python collection.py --safe           # jamais de redirection de pointeur
    python collection.py --undo
"""
from __future__ import annotations

import base64
import ctypes
import json
import os
import random
import subprocess
import sys
import time
import uuid
from ctypes import wintypes
from datetime import datetime
from pathlib import Path

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

ROOT = Path(__file__).resolve().parent
CACHE_FILE = ROOT / "cache" / "skins.json"
DUMP_DIR = ROOT / "cache"

LOCALAPPDATA = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
LOCKFILE = LOCALAPPDATA / "Riot Games" / "Riot Client" / "Config" / "lockfile"
RIOT_CLIENT_EXE = [
    Path(r"C:\Riot Games\Riot Client\RiotClientServices.exe"),
    Path(r"C:\Program Files\Riot Games\Riot Client\RiotClientServices.exe"),
]

API = "https://valorant-api.com/v1"
NIL = "00000000-0000-0000-0000-000000000000"
PROCESSUS = ["VALORANT-Win64-Shipping.exe", "VALORANT.exe"]

# ItemTypeID connus. On ne patche qu'un type si le compte le possède déjà
# (donc la clé existe vraiment dans la map) et si on a un catalogue pour lui.
TYPES = {
    "skins": "bcef87d6-209b-46c6-8b19-fbe40bd95abc",
    "levels": "e7c63390-eda7-46e0-bb7a-a6abdacd2433",
    "chromas": "3ad1b2b2-acdb-4524-852f-954a76ddae0a",
    "buddies": "d5f120f8-ff8c-4aac-92ea-f2b5acbe9475",
    "cards": "3f296c07-64c3-494c-923b-fe692a4fa1bd",
    "titles": "5649a249-ad0b-4c1d-87be-a2bd9b4d5b2c",
}
DEFAUT = ["skins", "levels", "chromas"]

PROCESS_VM_READ = 0x0010
PROCESS_VM_WRITE = 0x0020
PROCESS_VM_OPERATION = 0x0008
PROCESS_QUERY_INFORMATION = 0x0400

MEM_COMMIT = 0x1000
MEM_PRIVATE = 0x20000
MEM_RESERVE = 0x2000
PROTECTS = {0x02, 0x04, 0x08, 0x40, 0x80}
PAGE_READWRITE = 0x04

CHUNK = 4 * 1024 * 1024
PLAFOND = 0x7FFFFFFFFFFF
MAX_ITEMS = 6000
PLAFOND_TABLEAU = 100000

SHARD_BY_REGION = {
    "na": "na", "br": "na", "latam": "na", "lan": "na", "las": "na",
    "eu": "eu", "tr": "eu", "ru": "eu", "eune": "eu", "euw": "eu",
    "ap": "ap", "jp": "ap", "oce": "ap", "sea": "ap",
    "kr": "kr",
}

CLIENT_PLATFORM = base64.b64encode(json.dumps({
    "platformType": "PC", "platformOS": "Windows",
    "platformOSVersion": "10.0.19042.1.256.64bit", "platformChipset": "Unknown",
}).encode()).decode()

k32 = ctypes.WinDLL("kernel32", use_last_error=True)


class MBI(ctypes.Structure):
    _fields_ = [
        ("BaseAddress", ctypes.c_ulonglong),
        ("AllocationBase", ctypes.c_ulonglong),
        ("AllocationProtect", wintypes.DWORD),
        ("_pad1", wintypes.DWORD),
        ("RegionSize", ctypes.c_ulonglong),
        ("State", wintypes.DWORD),
        ("Protect", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("_pad2", wintypes.DWORD),
    ]


k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
k32.OpenProcess.restype = wintypes.HANDLE
k32.VirtualQueryEx.argtypes = [wintypes.HANDLE, ctypes.c_void_p,
                               ctypes.POINTER(MBI), ctypes.c_size_t]
k32.VirtualQueryEx.restype = ctypes.c_size_t
k32.ReadProcessMemory.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p,
                                  ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
k32.ReadProcessMemory.restype = wintypes.BOOL
k32.WriteProcessMemory.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p,
                                   ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
k32.WriteProcessMemory.restype = wintypes.BOOL
k32.VirtualAllocEx.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_size_t,
                               wintypes.DWORD, wintypes.DWORD]
k32.VirtualAllocEx.restype = ctypes.c_ulonglong
k32.CloseHandle.argtypes = [wintypes.HANDLE]


class UnlockError(RuntimeError):
    pass


def say(msg: str = "") -> None:
    print(msg, flush=True)


# --------------------------------------------------------------------------- #
# mémoire
# --------------------------------------------------------------------------- #

def pid_de(nom: str) -> int | None:
    sortie = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {nom}", "/FO", "CSV", "/NH"],
                            capture_output=True, timeout=10).stdout or b""
    for ligne in sortie.splitlines():
        if nom.encode().lower() in ligne.lower():
            morceaux = ligne.split(b'","')
            if len(morceaux) > 1:
                try:
                    return int(morceaux[1])
                except ValueError:
                    pass
    return None


def pid_jeu() -> int | None:
    for nom in PROCESSUS:
        pid = pid_de(nom)
        if pid:
            return pid
    return None


def ouvrir() -> int:
    if not ctypes.windll.shell32.IsUserAnAdmin():
        raise UnlockError("relance le terminal en administrateur")
    pid = pid_jeu()
    if not pid:
        raise UnlockError("processus du jeu introuvable")
    h = k32.OpenProcess(PROCESS_VM_READ | PROCESS_VM_WRITE | PROCESS_VM_OPERATION
                        | PROCESS_QUERY_INFORMATION, False, pid)
    if not h:
        raise UnlockError(f"OpenProcess refusé (erreur {ctypes.get_last_error()})")
    return h


def regions(h) -> list[tuple[int, int]]:
    sorties, mbi = [], MBI()
    adresse = 0x10000
    while adresse < PLAFOND:
        if not k32.VirtualQueryEx(h, ctypes.c_void_p(adresse), ctypes.byref(mbi),
                                  ctypes.sizeof(mbi)):
            break
        base, taille = mbi.BaseAddress, mbi.RegionSize
        if (mbi.State == MEM_COMMIT and mbi.Type == MEM_PRIVATE
                and mbi.Protect in PROTECTS and taille):
            sorties.append((base, taille))
        if not taille:
            break
        adresse = base + taille
    return sorties


def lire(h, adresse: int, taille: int) -> bytes | None:
    tampon = ctypes.create_string_buffer(taille)
    lus = ctypes.c_size_t()
    if not k32.ReadProcessMemory(h, ctypes.c_void_p(adresse), tampon, taille,
                                 ctypes.byref(lus)) or lus.value != taille:
        return None
    return tampon.raw


def ecrire(h, adresse: int, octets: bytes) -> bool:
    tampon = ctypes.create_string_buffer(octets, len(octets))
    ecrits = ctypes.c_size_t()
    return (bool(k32.WriteProcessMemory(h, ctypes.c_void_p(adresse), tampon,
                                        len(octets), ctypes.byref(ecrits)))
            and ecrits.value == len(octets))


def variantes(uuid_str: str) -> list[bytes]:
    u = uuid.UUID(uuid_str)
    return [u.bytes_le, u.bytes]


def en_le(uuid_str: str) -> bytes:
    return uuid.UUID(uuid_str).bytes_le


# --------------------------------------------------------------------------- #
# API locale
# --------------------------------------------------------------------------- #

def api_prete(timeout: float = 300.0) -> dict | None:
    """Attend que le client soit connecté et renvoie les en-têtes PD."""
    limite = time.monotonic() + timeout
    while time.monotonic() < limite:
        try:
            port, motdepasse, protocole = LOCKFILE.read_text(encoding="utf-8").strip().split(":")[1:]
            base = f"{protocole}://127.0.0.1:{port}"
            locaux = {"Authorization": "Basic " + base64.b64encode(
                f"riot:{motdepasse}".encode()).decode()}
            ent = requests.get(f"{base}/entitlements/v1/token", headers=locaux,
                               verify=False, timeout=5).json()
            region = requests.get(f"{base}/riotclient/region-locale", headers=locaux,
                                  verify=False, timeout=5).json()
            shard = region.get("shard") or SHARD_BY_REGION.get((region.get("region") or "").lower())
            if not shard:
                raise ValueError("shard")
            pd = f"https://pd.{shard}.a.pvp.net"
            entetes = {
                "Authorization": f"Bearer {ent['accessToken']}",
                "X-Riot-Entitlements-JWT": ent["token"],
                "X-Riot-ClientPlatform": CLIENT_PLATFORM,
                "Content-Type": "application/json",
            }
            puuid = ent["subject"]
            r = requests.get(f"{pd}/personalization/v3/players/{puuid}/playerloadout",
                             headers=entetes, verify=False, timeout=10)
            if r.status_code == 200:
                return {"pd": pd, "entetes": entetes, "puuid": puuid, "loadout": r.json()}
        except (OSError, ValueError, KeyError, requests.RequestException):
            pass
        time.sleep(2.0)
    return None


def types_presents(api: dict) -> set[str]:
    """Types réellement possédés : garantit que la clé existe dans la map."""
    try:
        r = requests.get(f"{api['pd']}/store/v1/entitlements/{api['puuid']}",
                         headers=api["entetes"], verify=False, timeout=20)
        blocs = (r.json() or {}).get("EntitlementsByTypes") or []
    except (requests.RequestException, ValueError):
        return set()
    presents = {b.get("ItemTypeID") for b in blocs}
    return {nom for nom, tid in TYPES.items() if tid in presents}


# --------------------------------------------------------------------------- #
# catalogue
# --------------------------------------------------------------------------- #

def catalogue(noms: set[str]) -> dict[str, list[str]]:
    pools: dict[str, list[str]] = {}

    def valides(items) -> list[str]:
        out = []
        for it in items:
            u = it.get("uuid")
            if u and u != NIL:
                try:
                    uuid.UUID(u)
                except ValueError:
                    continue
                out.append(u)
        return out

    def ajouter(cle: str, items) -> None:
        pool = valides(items)[:MAX_ITEMS]
        random.shuffle(pool)
        pools[cle] = pool
        say(f"  {cle:8} {len(pool)} item(s) au catalogue")

    if noms & {"skins", "levels", "chromas"}:
        cache = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        skins = [s for liste in cache["armes"].values() for s in liste]
        if "skins" in noms:
            ajouter("skins", skins)
        if "levels" in noms:
            ajouter("levels", [n for s in skins for n in s["niveaux"]])
        if "chromas" in noms:
            ajouter("chromas", [{"uuid": c} for s in skins for c in
                                [x for n in s["niveaux"] for x in n["chromas"]]])

    points = {"buddies": "buddies", "cards": "playercards", "titles": "playertitles"}
    for cle, route in points.items():
        if cle not in noms:
            continue
        try:
            data = requests.get(f"{API}/{route}", timeout=30).json().get("data") or []
        except (requests.RequestException, ValueError):
            continue
        ajouter(cle, data)
    return pools


# --------------------------------------------------------------------------- #
# localisation des tableaux
# --------------------------------------------------------------------------- #

class Tableau:
    def __init__(self, type_nom: str, entete: int, ptr: int, num: int,
                 capacite: int, pas: int) -> None:
        self.type = type_nom
        self.entete = entete
        self.ptr = ptr
        self.num = num
        self.capacite = capacite
        self.pas = pas

    def __str__(self) -> str:
        return f"{self.type} @ 0x{self.entete:X} ({self.num}/{self.capacite}, pas {self.pas})"


def valider(h, adresse: int, pool_le: set[bytes]) -> Tableau | None:
    for decalage in range(0x10, 0x70, 8):
        entete = lire(h, adresse + decalage, 16)
        if entete is None:
            continue
        ptr = int.from_bytes(entete[0:8], "little")
        num = int.from_bytes(entete[8:12], "little")
        capacite = int.from_bytes(entete[12:16], "little")
        if not ptr or num < 1 or capacite < num or capacite > PLAFOND_TABLEAU:
            continue
        contenu = lire(h, ptr, 16 * 8)
        if contenu is None:
            continue
        for pas in (32, 16):
            entrees = [contenu[i:i + 16] for i in range(0, 3 * pas, pas)]
            if any(e == b"\x00" * 16 for e in entrees):
                continue
            if sum(1 for e in entrees if e in pool_le) < 1:
                continue
            return Tableau("", adresse + decalage, ptr, num, capacite, pas)
    return None


def localiser(h, type_nom: str, pool: list[str]) -> Tableau | None:
    pool_le = {en_le(u) for u in pool}
    for motif in variantes(TYPES[type_nom]):
        for base, taille in regions(h):
            pos = 0
            while pos < taille:
                bloc = min(CHUNK, taille - pos)
                data = lire(h, base + pos, bloc)
                if data is None:
                    break
                i = data.find(motif)
                while i != -1:
                    trouve = valider(h, base + pos + i, pool_le)
                    if trouve:
                        trouve.type = type_nom
                        return trouve
                    i = data.find(motif, i + 1)
                pos += max(1, bloc - (len(motif) - 1))
    return None


# --------------------------------------------------------------------------- #
# remplissage
# --------------------------------------------------------------------------- #

def dump(tableau: Tableau, entete: bytes, contenu: bytes) -> Path:
    DUMP_DIR.mkdir(parents=True, exist_ok=True)
    chemin = DUMP_DIR / f"undo_collection_{tableau.type}_{datetime.now():%H%M%S}.json"
    chemin.write_text(json.dumps({
        "type": tableau.type,
        "entete": tableau.entete,
        "entete_octets": entete.hex(),
        "ptr": tableau.ptr,
        "num": tableau.num,
        "contenu_octets": contenu.hex(),
    }), encoding="utf-8")
    return chemin


def remplir(h, tableau: Tableau, pool: list[str], redirection: bool) -> tuple[int, bool]:
    """Remplit jusqu'à la capacité. Renvoie (écrits, redirigé)."""
    libre = max(0, tableau.capacite - tableau.num)
    quantite = min(libre, len(pool))
    ecrits = 0
    if quantite:
        tampon = b"".join(
            en_le(pool[i]) + uuid.uuid5(uuid.NAMESPACE_OID, pool[i]).bytes_le
            for i in range(quantite))
        if ecrire(h, tableau.ptr + tableau.num * tableau.pas, tampon):
            ecrits = quantite
            if not ecrire(h, tableau.entete + 8, (tableau.num + quantite).to_bytes(4, "little")):
                ecrits = 0
    tableau.num += ecrits

    if ecrits >= len(pool) or not redirection:
        return ecrits, False

    # capacité insuffisante : tampon externe + pointeur redirigé
    reste = pool[len(pool) - (len(pool) - ecrits):]
    total = len(pool)
    taille = total * 32
    memoire = k32.VirtualAllocEx(h, None, taille, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE)
    if not memoire:
        return ecrits, False
    tampon = b"".join(en_le(u) + uuid.uuid5(uuid.NAMESPACE_OID, u).bytes_le for u in pool)
    if not ecrire(h, memoire, tampon):
        return ecrits, False
    entete = (memoire.to_bytes(8, "little") + total.to_bytes(4, "little")
              + total.to_bytes(4, "little"))
    if not ecrire(h, tableau.entete, entete):
        return ecrits, False
    tableau.ptr, tableau.num, tableau.capacite, tableau.pas = memoire, total, total, 32
    return total, True


def surveiller(h, tableaux: list[tuple[Tableau, list[str]]], redirection: bool) -> None:
    say("\n  surveillance active — Ctrl+C pour arrêter")
    while True:
        time.sleep(2.0)
        if not pid_jeu():
            say("  jeu fermé")
            return
        for tableau, pool in tableaux:
            actuel = lire(h, tableau.ptr, 16)
            if actuel is None or actuel not in {en_le(u) for u in pool[:1]}:
                remettre(h, tableau, pool, redirection)


def remettre(h, tableau: Tableau, pool: list[str], redirection: bool) -> None:
    entete = lire(h, tableau.entete, 16)
    if entete is None:
        return
    ptr = int.from_bytes(entete[0:8], "little")
    num = int.from_bytes(entete[8:12], "little")
    capacite = int.from_bytes(entete[12:16], "little")
    if not ptr or capacite < 1 or capacite > PLAFOND_TABLEAU:
        return
    tableau.ptr, tableau.num, tableau.capacite = ptr, num, capacite
    remplir(h, tableau, pool, redirection)


def annuler(h) -> int:
    dumps = sorted(DUMP_DIR.glob("undo_collection_*.json"))
    if not dumps:
        raise UnlockError("aucun dump")
    faits = 0
    for chemin in dumps:
        data = json.loads(chemin.read_text(encoding="utf-8"))
        contenu = bytes.fromhex(data["contenu_octets"])
        if contenu and ecrire(h, data["ptr"], contenu):
            if ecrire(h, data["entete"], bytes.fromhex(data["entete_octets"])):
                faits += 1
        chemin.unlink()
    say(f"  {faits} tableau(x) restauré(s)")
    return faits


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #

def lancer_jeu() -> None:
    for exe in RIOT_CLIENT_EXE:
        if exe.exists():
            say(f"  lancement de {exe}")
            subprocess.Popen([str(exe), "--launch-product=valorant", "--launch-patchline=live"])
            return
    raise UnlockError("Riot Client introuvable, lance le jeu à la main")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if os.name != "nt":
        say("Windows uniquement")
        return 1
    if not ctypes.windll.shell32.IsUserAnAdmin():
        raise UnlockError("relance le terminal en administrateur")

    if "--undo" in sys.argv:
        h = ouvrir()
        try:
            annuler(h)
        finally:
            k32.CloseHandle(h)
        return 0

    if not CACHE_FILE.exists():
        raise UnlockError("cache absent : lance `python unlock.py --refresh --list` une fois")

    if "--attach" not in sys.argv and not pid_jeu():
        lancer_jeu()
        say("  attente du processus…")
        limite = time.monotonic() + 300
        while not pid_jeu() and time.monotonic() < limite:
            time.sleep(2.0)
        if not pid_jeu():
            raise UnlockError("le jeu ne démarre pas")

    say("  attente de la connexion au menu…")
    api = api_prete()
    if not api:
        raise UnlockError("connexion au menu non détectée (5 min)")

    presents = types_presents(api)
    demandes = sys.argv[sys.argv.index("--types") + 1].split(",") if "--types" in sys.argv else DEFAUT
    if "all" in demandes:
        demandes = list(TYPES)
    cibles = [t for t in demandes if t in TYPES and t in presents]
    if not cibles:
        raise UnlockError("aucun type exploitable sur ce compte")

    say(f"  types : {', '.join(cibles)}")
    say("  chargement du catalogue…")
    pools = catalogue(set(cibles))
    redirection = "--safe" not in sys.argv

    h = ouvrir()
    try:
        tableaux: list[tuple[Tableau, list[str]]] = []
        for nom in cibles:
            pool = pools.get(nom) or []
            if not pool:
                continue
            say(f"  recherche du tableau {nom}…")
            depart = time.monotonic()
            tableau = localiser(h, nom, pool)
            if not tableau:
                say(f"  {nom} : introuvable")
                continue
            say(f"  {tableau} trouvé en {time.monotonic() - depart:.1f}s")
            entete = lire(h, tableau.entete, 16) or b"\x00" * 16
            contenu = lire(h, tableau.ptr, tableau.num * tableau.pas) or b""
            if entete and contenu:
                dump(tableau, entete, contenu)
            ecrits, redirige = remplir(h, tableau, pool, redirection)
            say(f"  {nom} : {ecrits} entrée(s) écrite(s)"
                + (" (pointeur redirigé)" if redirige else ""))
            tableaux.append((tableau, pool))

        if not tableaux:
            raise UnlockError("rien n'a pu être localisé")
        say("\n  ouvre la collection, ferme-la et rouvre si l'affichage traîne")
        surveiller(h, tableaux, redirection)
    finally:
        k32.CloseHandle(h)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except UnlockError as exc:
        say(f"\n{exc}")
        raise SystemExit(1)
    except KeyboardInterrupt:
        say("\ninterrompu")