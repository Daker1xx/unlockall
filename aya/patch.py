#!/usr/bin/env python3
"""
Remplace une skin directement dans la mémoire du client — expérimental.

Principe : la skin équipée est un GUID de 16 octets présent en mémoire. On le
cherche, on l'écrase par le GUID de la skin voulue. Aucune injection de DLL,
aucun driver, aucun fichier touché.

Limites, à lire :
  - c'est local, les autres joueurs voient ta skin d'origine ;
  - ça ne survit ni à un redémarrage du jeu, ni à un changement de carte ;
  - les GUID de niveau et de chroma sont aussi remplacés quand ils sont trouvés ;
  - on ne sait pas filtrer les copies inutiles : on écrit partout où le GUID
    apparaît, ce qui est sémantiquement sûr (même valeur, même sens) ;
  - Riot classe ces outils en « skin hacks » : 14 jours au premier passage.

Prérequis :
  1. avoir lancé `python unlock.py --list` au moins une fois (cache) ;
  2. équiper une skin sur l'arme via l'API, même non possédée ;
  3. tenir l'arme en main dans le stand de tir, jeu au premier plan.

Usage :
    python patch.py --scan vandal            # combien d'occurrences, sans écrire
    python patch.py --weapon vandal --skin araxys
"""
from __future__ import annotations

import base64
import ctypes
import json
import os
import subprocess
import sys
import time
import uuid
from ctypes import wintypes
from pathlib import Path

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

ROOT = Path(__file__).resolve().parent
CACHE_FILE = ROOT / "cache" / "skins.json"

LOCALAPPDATA = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
LOCKFILE = LOCALAPPDATA / "Riot Games" / "Riot Client" / "Config" / "lockfile"
RIOT_CLIENT_EXE = [
    Path(r"C:\Riot Games\Riot Client\RiotClientServices.exe"),
    Path(r"C:\Program Files\Riot Games\Riot Client\RiotClientServices.exe"),
]

API = "https://valorant-api.com/v1"
NIL = "00000000-0000-0000-0000-000000000000"
PROCESSUS = ["VALORANT-Win64-Shipping.exe", "VALORANT.exe"]

PROCESS_VM_READ = 0x0010
PROCESS_VM_WRITE = 0x0020
PROCESS_VM_OPERATION = 0x0008
PROCESS_QUERY_INFORMATION = 0x0400

MEM_COMMIT = 0x1000
PROTECTS = {0x04, 0x08, 0x40, 0x80}   # RW, WRITECOPY, EXEC_RW, EXEC_WRITECOPY
CHUNK = 4 * 1024 * 1024
PLAFOND = 0x7FFFFFFFFFFF

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
k32.CloseHandle.argtypes = [wintypes.HANDLE]


class PatchError(RuntimeError):
    pass


def say(msg: str = "") -> None:
    print(msg, flush=True)


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


def motif(uuid_str: str) -> list[bytes]:
    u = uuid.UUID(uuid_str)
    return [u.bytes, u.bytes_le]


def regions(h) -> list[tuple[int, int]]:
    sorties = []
    mbi = MBI()
    adresse = 0
    while adresse < PLAFOND:
        if not k32.VirtualQueryEx(h, ctypes.c_void_p(adresse), ctypes.byref(mbi),
                                  ctypes.sizeof(mbi)):
            break
        base, taille = mbi.BaseAddress, mbi.RegionSize
        if mbi.State == MEM_COMMIT and mbi.Protect in PROTECTS and taille:
            sorties.append((base, taille))
        if not taille:
            break
        adresse = base + taille
    return sorties


def chercher(h, octets: bytes) -> list[int]:
    trouves = []
    for base, taille in regions(h):
        pos = 0
        while pos < taille:
            bloc = min(CHUNK, taille - pos)
            tampon = ctypes.create_string_buffer(bloc)
            lus = ctypes.c_size_t()
            ok = k32.ReadProcessMemory(h, ctypes.c_void_p(base + pos), tampon, bloc,
                                       ctypes.byref(lus))
            if not ok or not lus.value:
                break
            data = tampon.raw[:lus.value]
            i = data.find(octets)
            while i != -1:
                trouves.append(base + pos + i)
                i = data.find(octets, i + 1)
            pos += max(1, lus.value - (len(octets) - 1))
    return trouves


def ecrire(h, adresse: int, octets: bytes) -> bool:
    tampon = ctypes.create_string_buffer(octets, len(octets))
    ecrits = ctypes.c_size_t()
    return bool(k32.WriteProcessMemory(h, ctypes.c_void_p(adresse), tampon,
                                       len(octets), ctypes.byref(ecrits)))


# --------------------------------------------------------------------------- #
# API : lire le loadout, et le caler sur la cible
# --------------------------------------------------------------------------- #

def lire_lockfile() -> tuple[int, str, str]:
    brut = LOCKFILE.read_text(encoding="utf-8").strip()
    _, _pid, port, motdepasse, protocole = brut.split(":")
    return int(port), motdepasse, protocole


class Api:
    def __init__(self) -> None:
        self.http = requests.Session()
        port, motdepasse, protocole = lire_lockfile()
        base = f"{protocole}://127.0.0.1:{port}"
        entetes = {"Authorization": "Basic " + base64.b64encode(
            f"riot:{motdepasse}".encode()).decode()}
        ent = self._get(f"{base}/entitlements/v1/token", entetes)
        self.access, self.ent_token, self.puuid = ent["accessToken"], ent["token"], ent["subject"]
        region = self._get(f"{base}/riotclient/region-locale", entetes)
        shard = region.get("shard") or SHARD_BY_REGION.get((region.get("region") or "").lower())
        if not shard:
            raise PatchError("région impossible à résoudre")
        self.pd = f"https://pd.{shard}.a.pvp.net"
        self.entetes = {
            "Authorization": f"Bearer {self.access}",
            "X-Riot-Entitlements-JWT": self.ent_token,
            "X-Riot-ClientPlatform": CLIENT_PLATFORM,
            "Content-Type": "application/json",
        }

    @staticmethod
    def _get(url: str, entetes: dict) -> dict:
        r = requests.get(url, headers=entetes, verify=False, timeout=10)
        r.raise_for_status()
        return r.json()

    def _url(self) -> str:
        return f"{self.pd}/personalization/v3/players/{self.puuid}/playerloadout"

    def lire(self) -> dict:
        r = self.http.get(self._url(), headers=self.entetes, timeout=20)
        r.raise_for_status()
        return r.json()

    def ecrire(self, corps: dict) -> None:
        r = self.http.put(self._url(), headers=self.entetes, json=corps, timeout=20)
        if r.status_code >= 400:
            raise PatchError(f"PUT refusé -> {r.status_code} {r.text[:150]}")


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #

def charger_cache() -> tuple[dict, dict]:
    if not CACHE_FILE.exists():
        raise PatchError("cache absent : lance d'abord `python unlock.py --list`")
    brut = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    par_arme = brut.get("armes") or {}
    uuid_arme = brut.get("uuid_armes") or {}
    if not par_arme:
        raise PatchError("cache vide : relance `python unlock.py --refresh --list`")
    return par_arme, uuid_arme


def resoudre_arme(nom: str, par_arme: dict) -> str | None:
    nom = nom.strip().lower()
    for arme in par_arme:
        if arme.lower() == nom:
            return arme
    for arme in par_arme:
        if nom in arme.lower():
            return arme
    return None


def slot_de(body: dict, arme: str, uuid_arme: dict) -> dict | None:
    for canon in body.get("Guns") or []:
        if uuid_arme.get(canon.get("ID"), "").lower() == arme.lower():
            return canon
    melee = body.get("Melee")
    if isinstance(melee, dict) and arme.lower() == "melee":
        return melee
    return None


def appliquer(api: Api, arme: str, skin: dict, par_arme: dict) -> None:
    body = api.lire()
    cible = slot_de(body, arme, uuid_arme_cache)
    if cible is None:
        raise PatchError(f"{arme} absente du loadout")
    niveau = skin["niveaux"][-1]
    chroma = next((c["uuid"] for c in skin["chromas"] if c["uuid"] in niveau["chromas"]), NIL)
    cible["SkinID"] = skin["uuid"]
    cible["SkinLevelID"] = niveau["uuid"]
    cible["ChromaID"] = chroma
    if not isinstance(cible.get("Attachments"), list):
        cible["Attachments"] = []
    api.ecrire(body)
    say(f"  loadout calé sur {skin['nom']} (niveau {len(skin['niveaux'])})")


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    if os.name != "nt":
        say("Windows uniquement")
        return 1

    par_arme, uuid_arme = charger_cache()
    global uuid_arme_cache
    uuid_arme_cache = uuid_arme

    demande = None
    if "--scan" in sys.argv:
        demande = sys.argv[sys.argv.index("--scan") + 1]
    elif "--weapon" in sys.argv:
        demande = sys.argv[sys.argv.index("--weapon") + 1]
    else:
        say("usage : python patch.py --scan <arme> | --weapon <arme> --skin <filtre>")
        return 1

    arme = resoudre_arme(demande, par_arme)
    if not arme:
        say(f"  arme inconnue : {demande}")
        return 1

    api = Api()
    body = api.lire()
    cible = slot_de(body, arme, uuid_arme)
    if cible is None:
        say(f"  {arme} absente du loadout")
        return 1

    actuel = cible.get("SkinID")
    if actuel in (None, NIL):
        say("  aucune skin équipée sur cette arme : équipe-en une via unlock.py")
        return 1

    skin = None
    if "--skin" in sys.argv:
        filtre = sys.argv[sys.argv.index("--skin") + 1].lower()
        candidats = [s for s in par_arme[arme] if filtre in s["nom"].lower()]
        if not candidats:
            say(f"  aucune skin ne contient « {filtre} » sur {arme}")
            return 1
        skin = candidats[0]
        appliquer(api, arme, skin, par_arme)
        body = api.lire()
        cible = slot_de(body, arme, uuid_arme) or cible

    pid = None
    for nom in PROCESSUS:
        pid = pid_de(nom)
        if pid:
            say(f"  processus : {nom} (pid {pid})")
            break
    if not pid:
        say("  jeu introuvable : lance VALORANT et tiens l'arme en main")
        return 1

    h = k32.OpenProcess(PROCESS_VM_READ | PROCESS_VM_WRITE | PROCESS_VM_OPERATION
                        | PROCESS_QUERY_INFORMATION, False, pid)
    if not h:
        say("  OpenProcess refusé — lance le script en administrateur")
        return 1

    try:
        say("  scan de la mémoire…")
        depart = time.monotonic()
        paires = [(cible.get("SkinID"), (skin or {}).get("uuid"))]
        if skin:
            paires.append((cible.get("SkinLevelID"), skin["niveaux"][-1]["uuid"]))
            chroma = next((c["uuid"] for c in skin["chromas"]
                           if c["uuid"] in skin["niveaux"][-1]["chromas"]), None)
            if chroma:
                paires.append((cible.get("ChromaID"), chroma))

        for source, destination in paires:
            if not source or source == NIL:
                continue
            trouve = []
            for octets in motif(source):
                trouve += chercher(h, octets)
            nom = "skin" if source == cible.get("SkinID") else "niveau/chroma"
            say(f"  {nom:12} {len(trouve)} occurrence(s)")
            if not skin or not destination:
                continue
            for adresse in trouve:
                ecrire(h, adresse, uuid.UUID(destination).bytes_le
                       if len(motif(source)[1]) == 16 and
                       source == cible.get("SkinID") else uuid.UUID(destination).bytes_le)
        say(f"  terminé en {time.monotonic() - depart:.1f}s")
        if skin:
            say("  change d'arme et reviens pour forcer le rendu")
    finally:
        k32.CloseHandle(h)
    return 0


uuid_arme_cache: dict = {}

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except PatchError as exc:
        say(f"\n{exc}")
        raise SystemExit(1)
    except KeyboardInterrupt:
        say("\ninterrompu")