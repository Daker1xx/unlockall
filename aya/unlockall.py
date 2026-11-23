#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
AYA v3.4 - unlockall client-side VALORANT
moteur FGuid (Unreal) + persistance du loadout via l'API du client

  python unlockall.py --prep           coupe Vanguard, charge dbk64, s'arrete
  python unlockall.py --fixdriver      remplace dbk64.sys par celui d'un Cheat Engine installe
  python unlockall.py                  menu (charge le driver si besoin)
  python unlockall.py --full           patch collection + equipement complet + PUT + surveillance
  python unlockall.py --vandal         selecteur de skin Vandal (mem + PUT)
  python unlockall.py --weapon vandal --skin prime --chroma 3
  python unlockall.py --collection     deverrouille la collection (sans equiper)
  python unlockall.py --api-only       equipement par API seule (sans toucher la memoire)
  python unlockall.py --state          etat du patch
  python unlockall.py --diag           diagnostic -> cache/diag.txt
  python unlockall.py --undo           restaure les tableaux d'origine
  python unlockall.py --restore        remet ton loadout d'origine (PUT)
  python unlockall.py --deep           insertion des types absents (alloc)
  python unlockall.py --driver         force la preparation du driver au demarrage
  python unlockall.py --nowatch        applique puis rend la main
  python unlockall.py --stop           ferme VALORANT et le Riot Client
"""
from __future__ import annotations

import base64
import ctypes
import json
import os
import random
import shutil
import subprocess
import sys
import time
import uuid
from ctypes import wintypes
from datetime import datetime
from pathlib import Path

try:
    import requests
    import urllib3
except ImportError:
    raise SystemExit("pip install requests")
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

ROOT = Path(__file__).resolve().parent
CACHE = ROOT / "cache"
CATALOGUE_FILE = CACHE / "catalog.json"
LOG_FILE = CACHE / "aya.log"
BACKUP = ROOT / "loadout_backup.json"
SAUVE = CACHE / "loadout_orig.json"
DBK_FILE = ROOT / "dbk64.sys"
LOCALAPPDATA = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
LOCKFILE = LOCALAPPDATA / "Riot Games" / "Riot Client" / "Config" / "lockfile"
RIOT = [Path(r"C:\Riot Games\Riot Client\RiotClientServices.exe"),
        Path(r"C:\Program Files\Riot Games\Riot Client\RiotClientServices.exe")]

API = "https://valorant-api.com/v1"
PROC = ["VALORANT-Win64-Shipping.exe", "VALORANT.exe"]
VANGUARD = ("vgk", "vgc")
CATALOGUE_VERSION = 8
MELEE = "4ade7faa-4cf1-8376-95ef-39884480959b"

TYPES = {
    "skins":        "bcef87d6-209b-46c6-8b19-fbe40bd95abc",
    "levels":       "e7c63390-eda7-46e0-bb7a-a6abdacd2433",
    "chromas":      "3ad1b2b2-acdb-4524-852f-954a76ddae0a",
    "buddies":      "77258665-71d1-4623-bc72-44db9bd5b3b3",
    "buddy_levels": "dd3bf334-87f3-40bd-b043-682a57a8dc3a",
    "cards":        "3f296c07-64c3-494c-923b-fe692a4fa1bd",
    "titles":       "de7caa6b-adf7-4588-bbd1-143831e786c6",
    "sprays":       "d5f120f8-ff8c-4aac-92ea-f2b5acbe9475",
    "agents":       "01bb38e1-da47-4e6a-9b1a-7662ef6729b1",
}
DEFAUT = ["skins", "levels", "chromas"]
NIL = "00000000-0000-0000-0000-000000000000"
NUL16 = b"\x00" * 16

VM_R, VM_W, VM_O, QI = 0x10, 0x20, 0x8, 0x400
MEM_COMMIT, MEM_PRIVATE, MEM_MAPPED, MEM_RESERVE = 0x1000, 0x20000, 0x40000, 0x2000
PAGE_READWRITE = 0x04
PROTS = {0x02, 0x04, 0x08, 0x20, 0x40, 0x80}
CHUNK = 4 * 1024 * 1024
PLAFOND = 0x7FFFFFFFFFFF
MAX_ITEMS = 20000
MAX_ARRAY = 20000
PAS_ENT = 32
OFF_NUM, OFF_MAX = 8, 12
OFF_FLAGS, OFF_HASHPTR, OFF_HASHSIZE = 0x10, 0x30, 0x40
MARGE_SLOT = 0x60
ORDRE = "fg"

SHARD = {"na": "na", "br": "na", "latam": "na", "lan": "na", "las": "na",
         "eu": "eu", "tr": "eu", "ru": "eu", "eune": "eu", "euw": "eu",
         "ap": "ap", "jp": "ap", "oce": "ap", "sea": "ap", "kr": "kr"}

PLATFORM = base64.b64encode(json.dumps({
    "platformType": "PC", "platformOS": "Windows",
    "platformOSVersion": "10.0.19042.1.256.64bit",
    "platformChipset": "Unknown"}).encode()).decode()

k32 = ctypes.WinDLL("kernel32", use_last_error=True)


class MBI(ctypes.Structure):
    _fields_ = [("BaseAddress", ctypes.c_ulonglong), ("AllocationBase", ctypes.c_ulonglong),
                ("AllocationProtect", wintypes.DWORD), ("_p1", wintypes.DWORD),
                ("RegionSize", ctypes.c_ulonglong), ("State", wintypes.DWORD),
                ("Protect", wintypes.DWORD), ("Type", wintypes.DWORD), ("_p2", wintypes.DWORD)]


k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
k32.OpenProcess.restype = wintypes.HANDLE
k32.CloseHandle.argtypes = [wintypes.HANDLE]
k32.VirtualQueryEx.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.POINTER(MBI), ctypes.c_size_t]
k32.VirtualQueryEx.restype = ctypes.c_size_t
k32.ReadProcessMemory.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t,
                                  ctypes.POINTER(ctypes.c_size_t)]
k32.ReadProcessMemory.restype = wintypes.BOOL
k32.WriteProcessMemory.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t,
                                   ctypes.POINTER(ctypes.c_size_t)]
k32.WriteProcessMemory.restype = wintypes.BOOL
k32.VirtualAllocEx.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_size_t, wintypes.DWORD,
                               wintypes.DWORD]
k32.VirtualAllocEx.restype = ctypes.c_ulonglong
k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                            wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
k32.CreateFileW.restype = wintypes.HANDLE
k32.DeviceIoControl.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD,
                                ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
                                ctypes.c_void_p]
k32.DeviceIoControl.restype = wintypes.BOOL


class Erreur(RuntimeError):
    pass


def log(m=""):
    print(m, flush=True)
    try:
        CACHE.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {m}\n")
    except OSError:
        pass


def elever():
    if ctypes.windll.shell32.IsUserAnAdmin():
        return True
    params = " ".join(f'"{a}"' for a in [str(Path(__file__).resolve()), *sys.argv[1:]])
    r = ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, params, str(ROOT), 1)
    return r > 32


# ------------------------------------------------------------ guid / layouts --
def _fguid(u):
    """GUID -> layout FGuid Unreal (4 uint32 little-endian)."""
    h = uuid.UUID(u).hex
    return b"".join(bytes.fromhex(h[i:i + 8])[::-1] for i in (0, 8, 16, 24))


def _depuis_fguid(b):
    """layout FGuid -> GUID."""
    if b is None or len(b) != 16:
        return None
    h = "".join(bytes(b[i:i + 4])[::-1].hex() for i in (0, 4, 8, 12))
    return f"{h[0:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"


def g(u):
    uu = uuid.UUID(u)
    if ORDRE == "fg":
        return _fguid(u)
    return uu.bytes_le if ORDRE == "le" else uu.bytes


def motifs(u):
    uu = uuid.UUID(u)
    out = [_fguid(u)]
    for b in (uu.bytes_le, uu.bytes):
        if b not in out:
            out.append(b)
    return tuple(out)


def guid_valide(b):
    if b is None or len(b) != 16 or b == NUL16:
        return False
    for cand in (b, _depuis_fguid(b)):
        if not cand:
            continue
        try:
            u = uuid.UUID(bytes=cand) if isinstance(cand, bytes) else uuid.UUID(cand)
        except (ValueError, AttributeError):
            continue
        if u.variant == uuid.RFC_4122 and u.version in (3, 4, 5):
            return True
    return False


def guid_ou_nil(b):
    return b is not None and len(b) == 16 and (b == NUL16 or guid_valide(b))


_TAB = []
for _i in range(256):
    _c = _i
    for _ in range(8):
        _c = (_c >> 1) ^ 0xEDB88320 if _c & 1 else _c >> 1
    _TAB.append(_c)


def hash_guid(b):
    c = 0
    for x in b:
        c = (c >> 8) ^ _TAB[(c ^ x) & 0xFF]
    return c


# -------------------------------------------------------- process / driver ---
def pid_de(nom):
    out = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {nom}", "/FO", "CSV", "/NH"],
                         capture_output=True, timeout=10).stdout or b""
    for l in out.splitlines():
        if nom.encode().lower() in l.lower():
            p = l.split(b'","')
            if len(p) > 1:
                try:
                    return int(p[1])
                except ValueError:
                    pass
    return None


def pid_jeu():
    for n in PROC:
        p = pid_de(n)
        if p:
            return p
    return None


def lancer_jeu():
    for exe in RIOT:
        if exe.exists():
            subprocess.Popen([str(exe), "--launch-product=valorant", "--launch-patchline=live"])
            return True
    return False


def arreter_jeu():
    for n in PROC + ["RiotClientServices.exe"]:
        subprocess.run(["taskkill", "/F", "/IM", n], capture_output=True, timeout=20)
    log("  jeu ferme")


def couper_vanguard():
    """Vanguard bloque le chargement de dbk64 : on le coupe avant de charger."""
    for n in VANGUARD:
        r = subprocess.run(["sc", "stop", n], capture_output=True, timeout=30)
        log(f"  sc stop {n} -> {r.returncode}")


DBK_DEV = r"\\.\DBK64"
DBK_READ = 0x22006C
DBK_WRITE = 0x220070
_DEV = [None]


class Dbk(ctypes.Structure):
    _fields_ = [("pid", wintypes.HANDLE), ("addr", ctypes.c_ulonglong),
                ("buf", ctypes.c_void_p), ("taille", ctypes.c_size_t),
                ("lus", ctypes.POINTER(ctypes.c_size_t))]


def device_driver():
    if _DEV[0]:
        return _DEV[0]
    for nom in (DBK_DEV, r"\\.\dbk64", r"\\.\DBK"):
        d = k32.CreateFileW(nom, 0xC0000000, 3, None, 3, 0, None)
        if d and d != wintypes.HANDLE(-1).value:
            _DEV[0] = d
            return d
    return None


def chercher_dbk_recent():
    """dbk64.sys d'une installation Cheat Engine, le plus recent possible."""
    cands = []
    for base in (Path(r"C:\Program Files"), Path(r"C:\Program Files (x86)"),
                 Path(os.environ.get("LOCALAPPDATA", "")) / "Programs",
                 Path(os.environ.get("PROGRAMFILES", r"C:\Program Files"))):
        try:
            for d in base.glob("Cheat Engine*"):
                f = d / "dbk64.sys"
                if f.exists():
                    cands.append(f)
        except OSError:
            pass
    if not cands:
        return None
    cands.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return cands[0]


def fixdriver():
    src = chercher_dbk_recent()
    if not src:
        log("  aucun dbk64.sys trouve : installe Cheat Engine 7.6 puis relance --fixdriver")
        return False
    if DBK_FILE.exists():
        shutil.copy2(DBK_FILE, DBK_FILE.with_suffix(".sys.bak"))
    shutil.copy2(src, DBK_FILE)
    log(f"  dbk64.sys remplace par {src}")
    log(f"  taille : {DBK_FILE.stat().st_size} octets")
    return True


def aide_driver():
    log("  --- chargement du driver bloque ---")
    log("  1. blocklist de pilotes vulnerables (console admin) :")
    log(r'     reg add "HKLM\SYSTEM\CurrentControlSet\Control\CI\Config" '
        r'/v VulnerableDriverBlockListEnable /t REG_DWORD /d 0 /f')
    log("     puis redemarrage")
    log("  2. dbk64 trop ancien -> installe Cheat Engine 7.6 puis : --fixdriver")
    log("  3. sinon ouvre Cheat Engine, Settings > Debugger > kernelmode,")
    log("     attache toi a VALORANT : CE charge le driver, AYA le reprend ensuite")
    log("  4. en dernier recours : bcdedit /set testsigning on + redemarrage")


def charger_driver():
    if device_driver():
        log("  driver deja actif (charge par Cheat Engine ?)")
        return True
    if not DBK_FILE.exists():
        log("  dbk64.sys absent du dossier")
        return False
    if not ctypes.windll.shell32.IsUserAnAdmin():
        log("  admin requis pour charger le driver")
        return False
    couper_vanguard()
    time.sleep(1.0)
    subprocess.run(["sc", "create", "DBK64", "type=", "kernel", "start=", "demand",
                    "binPath=", str(DBK_FILE)], capture_output=True, timeout=20)
    r = subprocess.run(["sc", "start", "DBK64"], capture_output=True, timeout=25)
    q = subprocess.run(["sc", "query", "DBK64"], capture_output=True, timeout=15)
    log(f"  sc start -> {r.returncode} | "
        f"{' '.join(q.stdout.decode('utf-8', 'replace').split())[:130]}")
    for _ in range(6):
        if device_driver():
            log("  driver dbk64 charge")
            return True
        time.sleep(0.5)
    log("  chargement refuse")
    aide_driver()
    return False


def preparer():
    """ordre obligatoire : jeu ferme -> Vanguard coupe -> driver -> jeu relance."""
    if pid_jeu():
        arreter_jeu()
        time.sleep(2.0)
    return charger_driver()


class Cible:
    def __init__(self, pid, mode="user"):
        if not pid:
            raise Erreur("aucun processus de jeu")
        self.pid = pid
        self.h = None
        self.dev = None
        self.mode = None
        self.droits = 0
        if mode == "driver" and charger_driver():
            self.dev = _DEV[0]
            self.mode = "driver"
            return
        self._ouvrir_user()
        self.mode = "user"

    def _ouvrir_user(self):
        dernier = 0
        for _ in range(15):
            for droits in (VM_R | VM_W | VM_O | QI, VM_R | VM_O | QI, VM_R | QI):
                h = k32.OpenProcess(droits, False, self.pid)
                if h:
                    self.h, self.droits = h, droits
                    if droits != (VM_R | VM_W | VM_O | QI):
                        log(f"  ATTENTION droits reduits (0x{droits:X})")
                    return
                dernier = ctypes.get_last_error()
            time.sleep(1.0)
        raise Erreur(f"OpenProcess refuse ({dernier}) - relance avec --prep")

    def _ioctl(self, code, addr, buf, taille):
        lus = ctypes.c_size_t()
        req = Dbk(self.pid, addr, ctypes.cast(buf, ctypes.c_void_p), taille, ctypes.byref(lus))
        ret = wintypes.DWORD()
        ok = k32.DeviceIoControl(self.dev, code, ctypes.byref(req), ctypes.sizeof(req),
                                 None, 0, ctypes.byref(ret), None)
        return bool(ok) and lus.value == taille

    def lire(self, a, n):
        if n <= 0:
            return b""
        buf = ctypes.create_string_buffer(n)
        if self.mode == "driver":
            return buf.raw if self._ioctl(DBK_READ, a, buf, n) else None
        lus = ctypes.c_size_t()
        if not k32.ReadProcessMemory(self.h, ctypes.c_void_p(a), buf, n, ctypes.byref(lus)) \
                or lus.value != n:
            return None
        return buf.raw

    def ecrire(self, a, octets):
        buf = ctypes.create_string_buffer(octets, len(octets))
        if self.mode == "driver":
            return self._ioctl(DBK_WRITE, a, buf, len(octets))
        f = ctypes.c_size_t()
        return bool(k32.WriteProcessMemory(self.h, ctypes.c_void_p(a), buf, len(octets),
                                           ctypes.byref(f))) and f.value == len(octets)

    def allouer(self, n):
        if self.mode != "user" or not (self.droits & VM_W):
            return 0
        return k32.VirtualAllocEx(self.h, None, n, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE) or 0

    def regions(self):
        out, mbi = [], MBI()
        a = 0x10000
        while a < PLAFOND:
            if not k32.VirtualQueryEx(self.h, ctypes.c_void_p(a), ctypes.byref(mbi), ctypes.sizeof(mbi)):
                break
            b, t = mbi.BaseAddress, mbi.RegionSize
            if mbi.State == MEM_COMMIT and mbi.Type in (MEM_PRIVATE, MEM_MAPPED) \
                    and mbi.Protect in PROTS and t:
                out.append((b, t))
            if not t:
                break
            a = b + t
        return out

    def fermer(self):
        if self.h:
            k32.CloseHandle(self.h)
            self.h = None
        if self.dev:
            k32.CloseHandle(self.dev)
            self.dev = None
            _DEV[0] = None


def _parcourir(c, base, taille, motif, rappel):
    """lit une region par tranches ; une page morte n'arrete plus tout."""
    pos = 0
    while pos < taille:
        bloc = min(CHUNK, taille - pos)
        data = c.lire(base + pos, bloc)
        if data is None:
            pos += 0x1000
            continue
        i = data.find(motif)
        while i != -1:
            res = rappel(base + pos + i)
            if res:
                return res
            i = data.find(motif, i + 1)
        pos += max(1, bloc - 15)
    return None


# --------------------------------------------------------------------- API ---
def version_client(base, loc):
    """version shipping du jeu ; le hash 16 hex du Riot Client ne suffit pas."""
    def plausible(v):
        return isinstance(v, str) and v.startswith("release-") and "shipping" in v
    for src in ("api", "local"):
        try:
            if src == "api":
                v = requests.get(f"{API}/version", timeout=10).json()["data"]["riotClientVersion"]
            else:
                s = requests.get(f"{base}/product-session/v1/external-sessions", headers=loc,
                                 verify=False, timeout=5).json()
                v = next((x["version"] for x in s.values()
                          if x.get("productId") == "valorant" and x.get("version")), "")
            if plausible(v):
                return v
        except Exception:
            pass
    return ""


def contexte(timeout=600.0):
    sess = requests.Session()
    sess.trust_env = False
    sess.verify = False
    fin = time.monotonic() + timeout
    dernier = None
    while time.monotonic() < fin:
        try:
            parties = LOCKFILE.read_text(encoding="utf-8").strip().split(":")
            if len(parties) != 5:
                raise Erreur(f"lockfile illisible ({len(parties)} champs)")
            port, pwd, proto = parties[2], parties[3], parties[4]
            base = f"{proto}://127.0.0.1:{port}"
            loc = {"Authorization": "Basic " + base64.b64encode(f"riot:{pwd}".encode()).decode()}
            r = sess.get(f"{base}/entitlements/v1/token", headers=loc, timeout=15)
            if r.status_code != 200:
                raise Erreur(f"token -> HTTP {r.status_code} {r.text[:80]}")
            ent = r.json()
            reg = sess.get(f"{base}/riotclient/region-locale", headers=loc, timeout=15).json()
            shard = reg.get("shard") or SHARD.get((reg.get("region") or "").lower())
            if not shard:
                raise Erreur(f"shard introuvable : {reg}")
            pd = f"https://pd.{shard}.a.pvp.net"
            entetes = {"Authorization": f"Bearer {ent['accessToken']}",
                       "X-Riot-Entitlements-JWT": ent["token"],
                       "X-Riot-ClientPlatform": PLATFORM,
                       "Content-Type": "application/json"}
            ver = version_client(base, loc)
            log(f"  version client : {ver or 'INCONNUE'}")
            if ver:
                entetes["X-Riot-ClientVersion"] = ver
            puuid = ent["subject"]
            loadout, url = None, None
            for v in ("v1", "v2", "v3"):
                u = f"{pd}/personalization/{v}/players/{puuid}/playerloadout"
                r2 = sess.get(u, headers=entetes, timeout=20)
                if r2.status_code == 200:
                    loadout, url = r2.json(), u
                    log(f"  loadout lu (personalization {v})")
                    break
            if loadout is None:
                raise Erreur(f"loadout -> HTTP {r2.status_code} {r2.text[:120]}")
            return {"pd": pd, "entetes": entetes, "puuid": puuid,
                    "loadout": loadout, "url": url}
        except Exception as e:
            m = f"{type(e).__name__}: {e}"
            if m != dernier:
                log(f"  -> {m}")
                dernier = m
        time.sleep(3.0)
    raise Erreur("API du client injoignable")


def _lire_blocs(brut):
    """accepte plusieurs formes de reponse pour EntitlementsByTypes."""
    if not isinstance(brut, dict):
        return []
    for cle in ("EntitlementsByTypes", "EntitlementsByType", "Entitlements"):
        v = brut.get(cle)
        if isinstance(v, list) and v:
            if isinstance(v[0], dict) and "ItemTypeID" in v[0]:
                return v
        elif isinstance(v, dict) and v:
            out = [{"ItemTypeID": k, "Entitlements": (x if isinstance(x, list) else [x])}
                   for k, x in v.items()]
            if out:
                return out
    return []


def blocs_entitlements(ctx):
    url = f"{ctx['pd']}/store/v1/entitlements/{ctx['puuid']}"
    for _ in range(3):
        try:
            r = requests.get(url, headers=ctx["entetes"], verify=False, timeout=20)
        except requests.RequestException as e:
            log(f"  entitlements : {type(e).__name__}")
        else:
            if r.status_code == 200:
                brut = r.json() or {}
                blocs = _lire_blocs(brut)
                if blocs:
                    log(f"  entitlements : {len(blocs)} type(s)")
                else:
                    log(f"  entitlements vide, cles={sorted(brut)[:8]}")
                    log(f"  brut={json.dumps(brut)[:240]}")
                return blocs
            log(f"  entitlements -> HTTP {r.status_code} {r.text[:120]}")
            if r.status_code in (401, 403):
                time.sleep(2)
                continue
            break
    return []


def slots(body):
    out = [s for s in (body.get("Guns") or []) if isinstance(s, dict)]
    if isinstance(body.get("Melee"), dict):
        out.append(body["Melee"])
    return out


def ancres_depuis_loadout(ctx):
    """les items equipes sont possedes : ancres de secours quand l'API ne repond pas."""
    out = {}
    for s in slots(ctx["loadout"]):
        if s.get("SkinID") and s["SkinID"] != NIL:
            out.setdefault("skins", []).append(s["SkinID"])
            if s.get("SkinLevelID") and s["SkinLevelID"] != NIL:
                out.setdefault("levels", []).append(s["SkinLevelID"])
            if s.get("ChromaID") and s["ChromaID"] != NIL:
                out.setdefault("chromas", []).append(s["ChromaID"])
    ident = ctx["loadout"].get("Identity") or {}
    for cle, nom in (("PlayerCardID", "cards"), ("PlayerTitleID", "titles")):
        if ident.get(cle) and ident[cle] != NIL:
            out.setdefault(nom, []).append(ident[cle])
    return {k: list(dict.fromkeys(v)) for k, v in out.items()}


def put_loadout(ctx, voulu):
    """voulu : {slot_id: (skin, niveau, chroma)} -> PUT reel, verifie par relecture."""
    if not voulu:
        return 0
    body = json.loads(json.dumps(ctx["loadout"]))
    n = 0
    for s in slots(body):
        v = voulu.get(s.get("ID"))
        if not v:
            continue
        s["SkinID"], s["SkinLevelID"], s["ChromaID"] = v
        s["Attachments"] = []
        n += 1
    if not n:
        return 0
    r = requests.put(ctx["url"], headers=ctx["entetes"], json=body, verify=False, timeout=25)
    if r.status_code not in (200, 201, 204):
        log(f"  PUT loadout -> HTTP {r.status_code} {r.text[:140]}")
        return 0
    time.sleep(0.6)
    try:
        ctl = requests.get(ctx["url"], headers=ctx["entetes"], verify=False, timeout=20).json()
        ok = {s["ID"]: s.get("SkinID") for s in slots(ctl) if s.get("ID") in voulu}
        valide = sum(1 for k, v in voulu.items() if ok.get(k) == v[0])
        if valide < len(voulu):
            log(f"  PUT partiel : {valide}/{len(voulu)} skins tenus par le serveur")
        return valide
    except (requests.RequestException, ValueError):
        return n


def sauver_loadout(ctx):
    if SAUVE.exists():
        return
    CACHE.mkdir(parents=True, exist_ok=True)
    SAUVE.write_text(json.dumps(ctx["loadout"]), encoding="utf-8")
    log(f"  loadout d'origine sauve -> {SAUVE.name}")


# --------------------------------------------------------------- catalogue ---
def _pool_propre(v):
    v = [u for u in (v or []) if isinstance(u, str) and u and u != NIL]
    return list(dict.fromkeys(v))


def _normaliser(brut):
    pools = {k: [] for k in TYPES}
    armes, armes_id, skin_arme = {}, {}, {}

    if isinstance(brut.get("pools"), dict):
        for k, v in brut["pools"].items():
            pools.setdefault(k, []).extend(v or [])
        armes = {k: v for k, v in (brut.get("armes") or {}).items() if v}
        armes_id = {k: v for k, v in (brut.get("armes_id") or {}).items() if v}
        for nom, skins in armes.items():
            for s in skins:
                skin_arme[s["uuid"]] = nom
    else:
        armes_id = {k: v for k, v in (brut.get("uuid_armes") or {}).items() if v}
        for s in brut.get("skins") or []:
            if not s.get("uuid") or s["uuid"] == NIL:
                continue
            nom = s.get("weapon") or s.get("arme") or "?"
            if s.get("weapon_uuid") or s.get("arme_uuid"):
                armes_id[s.get("weapon_uuid") or s.get("arme_uuid")] = nom
            skin_chromas = [{"uuid": c.get("uuid"), "nom": c.get("nom") or c.get("name") or ""}
                            for c in (s.get("chromas") or [])
                            if isinstance(c, dict) and c.get("uuid") and c["uuid"] != NIL]
            niveaux = []
            for n in s.get("levels") or s.get("niveaux") or []:
                if not n.get("uuid") or n["uuid"] == NIL:
                    continue
                chromas = [{"uuid": c.get("uuid"), "nom": c.get("nom") or ""}
                           for c in (n.get("chromas") or [])
                           if isinstance(c, dict) and c.get("uuid") and c["uuid"] != NIL]
                if not chromas:
                    chromas = list(skin_chromas)
                niveaux.append({"uuid": n["uuid"], "nom": n.get("nom") or "", "chromas": chromas})
                pools["levels"].append(n["uuid"])
                pools["chromas"] += [c["uuid"] for c in chromas]
            if not niveaux:
                continue
            pools["skins"].append(s["uuid"])
            skin_arme[s["uuid"]] = nom
            armes.setdefault(nom, []).append(
                {"uuid": s["uuid"], "nom": s.get("nom") or s.get("name") or "", "niveaux": niveaux})

        extras = brut.get("extras") or {}
        for k in ("buddies", "buddy_levels", "cards", "titles", "sprays", "agents"):
            pools[k].extend(extras.get(k) or [])

    for nom in list(armes):
        armes[nom] = sorted(armes[nom], key=lambda x: (x.get("nom") or "").lower())
        if not armes[nom]:
            armes.pop(nom)
    for k in list(pools):
        pools[k] = _pool_propre(pools[k])
        random.shuffle(pools[k])
        pools[k] = pools[k][:MAX_ITEMS]
    return {"version": CATALOGUE_VERSION, "armes": armes, "armes_id": armes_id,
            "skin_arme": skin_arme, "pools": pools}


def _construire():
    def get(route):
        r = requests.get(f"{API}/{route}", timeout=40)
        r.raise_for_status()
        return (r.json() or {}).get("data") or []

    pools = {k: [] for k in TYPES}
    armes, armes_id = {}, {}

    for a in get("weapons"):
        nom = a.get("displayName") or "?"
        if a.get("uuid"):
            armes_id[a["uuid"]] = nom
        skins = []
        for s in a.get("skins") or []:
            if not s.get("levels") or s["uuid"] == NIL:
                continue
            skin_chromas = [{"uuid": c["uuid"], "nom": c.get("displayName") or ""}
                            for c in s.get("chromas") or [] if c.get("uuid") and c["uuid"] != NIL]
            niveaux = []
            for n in s["levels"]:
                if not n.get("uuid") or n["uuid"] == NIL:
                    continue
                chromas = [{"uuid": c["uuid"], "nom": c.get("displayName") or ""}
                           for c in n.get("chromas") or [] if c.get("uuid") and c["uuid"] != NIL]
                if not chromas:
                    chromas = list(skin_chromas)
                niveaux.append({"uuid": n["uuid"], "nom": n.get("displayName") or "", "chromas": chromas})
                pools["levels"].append(n["uuid"])
                pools["chromas"] += [c["uuid"] for c in chromas]
            if not niveaux:
                continue
            pools["skins"].append(s["uuid"])
            skins.append({"uuid": s["uuid"], "nom": s.get("displayName") or "", "niveaux": niveaux})
        if skins:
            armes[nom] = skins

    for route, cle, sous in (("buddies", "buddies", "levels"),
                             ("sprays", "sprays", "levels"),
                             ("playercards", "cards", None),
                             ("playertitles", "titles", None),
                             ("agents", "agents", None)):
        try:
            for it in get(route):
                if it.get("uuid") and it["uuid"] != NIL:
                    pools[cle].append(it["uuid"])
                for s in (it.get(sous) or []) if sous else []:
                    if s.get("uuid") and s["uuid"] != NIL:
                        pools[cle].append(s["uuid"])
        except requests.RequestException as e:
            log(f"  {route} : {e}")

    return _normaliser({"pools": pools, "armes": armes, "armes_id": armes_id})


def _ecrire_cache(cat):
    CACHE.mkdir(parents=True, exist_ok=True)
    tmp = CATALOGUE_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(cat), encoding="utf-8")
    tmp.replace(CATALOGUE_FILE)


def catalogue(force=False):
    if force and CATALOGUE_FILE.exists():
        CATALOGUE_FILE.unlink()
    if CATALOGUE_FILE.exists():
        try:
            brut = json.loads(CATALOGUE_FILE.read_text(encoding="utf-8"))
            if not isinstance(brut, dict):
                raise ValueError("catalogue non-objet")
            if brut.get("version") == CATALOGUE_VERSION and brut.get("pools") and brut.get("armes"):
                return brut
            cat = _normaliser(brut)
            if cat["armes"] and cat["pools"].get("chromas"):
                _ecrire_cache(cat)
                return cat
        except (OSError, ValueError) as e:
            log(f"  cache illisible ({e}) -> reconstruction")
    try:
        cat = _construire()
    except (requests.RequestException, ValueError) as e:
        raise Erreur(f"catalogue indisponible : {e}")
    if not cat["armes"]:
        raise Erreur("catalogue vide")
    _ecrire_cache(cat)
    return cat


def etiqueter(blocs, cat):
    labels, ancres = {}, {}
    for b in blocs:
        t = b.get("ItemTypeID")
        ids = [e.get("ItemID") for e in b.get("Entitlements") or [] if e.get("ItemID")]
        if not t or not ids:
            continue
        ancres.setdefault(t, ids)
        jeu = set(ids)
        for nom, pool in cat["pools"].items():
            if nom in labels:
                continue
            if jeu & set(pool[:6000]):
                labels[t] = nom
                break
    return labels, ancres


# --------------------------------------------------- localisation par valeur --
def carte_depuis(c, entete, pas):
    for k in range(1, 600):
        obj = entete - 24 - k * pas
        ent = c.lire(obj, 0x44)
        if not ent:
            continue
        data = int.from_bytes(ent[0:8], "little")
        num = int.from_bytes(ent[OFF_NUM:OFF_NUM + 4], "little")
        mx = int.from_bytes(ent[OFF_MAX:OFF_MAX + 4], "little")
        if data and data % 16 == 0 and num < mx < 8192 and data <= entete \
                and (entete - 24 - data) % pas == 0:
            return obj, data, num, mx, ent
    return None


def _paire(c, a):
    b = c.lire(a, 32)
    return b if (b and guid_valide(b[0:16]) and guid_valide(b[16:32])) else None


def _depuis_valeur(c, a, pool_jeu, region):
    s = a
    for _ in range(MAX_ARRAY):
        if not _paire(c, s - 32):
            break
        s -= 32
    e = a + 32
    for _ in range(MAX_ARRAY):
        if not _paire(c, e):
            break
        e += 32
    entete = s - 16
    hd = c.lire(entete, 16)
    if not hd:
        return None
    ptr = int.from_bytes(hd[0:8], "little")
    num = int.from_bytes(hd[8:12], "little")
    cap = int.from_bytes(hd[12:16], "little")
    total = (e - s) // 32
    if ptr != s or total < 2 or num < total or cap < num or cap > 400000:
        return None
    pas = cle = None
    for cand, recul in ((40, 24), (48, 32)):
        kv = c.lire(entete - recul, 16)
        if kv and guid_valide(kv):
            pas, cle = cand, kv
            break
    if pas is None or not carte_depuis(c, entete, pas):
        return None
    contenu = c.lire(s, min(total, 400) * PAS_ENT)
    if not contenu:
        return None
    score = 0
    for j in range(len(contenu) // 32):
        if contenu[j * 32:j * 32 + 16] in pool_jeu:
            score += 2
    if score == 0:
        return None
    return {"entete": entete, "ptr": s, "num": num, "cap": cap, "pas": pas,
            "cle": cle.hex(), "region": region, "score": score}


def localiser_valeur(c, itemid, pool_jeu):
    for m in motifs(itemid):
        for base, taille in c.regions():
            res = _parcourir(c, base, taille, m,
                              lambda a: _depuis_valeur(c, a, pool_jeu, (base, taille)))
            if res:
                return res
    return None


def localiser_region(c, itemid, pool_jeu, region):
    base, taille = region
    for m in motifs(itemid):
        res = _parcourir(c, base, taille, m, lambda a: _depuis_valeur(c, a, pool_jeu, region))
        if res:
            return res
    return None


def localiser(c, nom, pool, ancres):
    pool_jeu = {g(u) for u in pool[:3000]}
    meilleur = None
    for it in ancres[:6]:
        res = localiser_valeur(c, it, pool_jeu)
        if res and (meilleur is None or res["score"] > meilleur["score"]):
            res["type"] = nom
            meilleur = res
    return meilleur


def detecter_ordre(c, ancres):
    """fg = FGuid (attendu) ; le/be en repli."""
    global ORDRE
    temoin = next((a for l in ancres.values() for a in l[:1]), None)
    if not temoin:
        return ORDRE
    cands = [("fg", _fguid(temoin)), ("le", uuid.UUID(temoin).bytes_le), ("be", uuid.UUID(temoin).bytes)]
    for nom, m in cands:
        for base, taille in c.regions():
            if _parcourir(c, base, taille, m, lambda a: True):
                ORDRE = nom
                return nom
    return ORDRE


# -------------------------------------------------------------------- diag ---
MARQUEURS = [b"EquippableSkin", b"ItemTypeID", b"Entitlement", b"VALORANT"]


def diag(c, ctx, cat, ancres):
    regs = c.regions()
    total = sum(t for _, t in regs)
    log(f"  regions={len(regs)} octets={total} mode={c.mode}")
    lignes = [f"mode={c.mode} regions={len(regs)} octets={total}", f"ordre={ORDRE}"]
    for m in MARQUEURS:
        n = 0
        for base, taille in regs:
            pos = 0
            while pos < taille:
                bloc = min(CHUNK, taille - pos)
                d = c.lire(base + pos, bloc)
                if d is None:
                    pos += 0x1000
                    continue
                n += d.count(m)
                pos += max(1, bloc - 15)
        lignes.append(f"ascii {m.decode():16} : {n}")
        log(f"  ascii {m.decode():16} : {n}")
    for nom, ids in ancres.items():
        pool_jeu = {g(u) for u in (cat["pools"].get(nom) or [])[:3000]}
        res = localiser_valeur(c, ids[0], pool_jeu) if pool_jeu else None
        if res:
            lignes.append(f"{nom:12} TROUVE ptr={res['ptr']:X} num={res['num']} cap={res['cap']} "
                          f"pas={res['pas']} score={res['score']}")
            log(f"  {nom:12} TROUVE num={res['num']} cap={res['cap']}")
        else:
            lignes.append(f"{nom:12} non localise")
            log(f"  {nom:12} non localise")
    CACHE.mkdir(parents=True, exist_ok=True)
    (CACHE / "diag.txt").write_text("\n".join(lignes), encoding="utf-8")
    log(f"  diagnostic -> {CACHE / 'diag.txt'}")


# --------------------------------------------------------- insertion de cle --
def _cle_presente(c, carte, cle, pas):
    obj, data, num, mx, ent = carte
    for i in range(min(num, 128)):
        if c.lire(data + i * pas + 8, 16) == cle:
            return True
    return False


def inserer(c, nom, guid_type, pool, tableaux):
    if not guid_type:
        return None
    cle = g(guid_type)
    for t in [x for x in tableaux.values() if not x.get("insere")]:
        pas = t["pas"]
        carte = carte_depuis(c, t["entete"], pas)
        if not carte or _cle_presente(c, carte, cle, pas):
            continue
        obj, data, num, mx, ent = carte
        if num >= mx:
            continue
        off_val = 24 if pas == 40 else 32
        el = data + num * pas
        ptr = c.allouer(len(pool) * PAS_ENT)
        if not ptr:
            return None
        tampon = b"".join(g(u) + uuid.uuid5(uuid.NAMESPACE_OID, u).bytes_le for u in pool)
        if not c.ecrire(ptr, tampon) or not c.ecrire(el + 8, cle):
            return None
        if not c.ecrire(el + off_val, ptr.to_bytes(8, "little") + len(pool).to_bytes(4, "little") * 2):
            return None
        flags = int.from_bytes(ent[OFF_FLAGS:OFF_FLAGS + 8], "little")
        if flags:
            mot = flags + (num >> 5) * 4
            v = c.lire(mot, 4)
            if v:
                c.ecrire(mot, (int.from_bytes(v, "little") | (1 << (num & 31))).to_bytes(4, "little"))
        hashptr = int.from_bytes(ent[OFF_HASHPTR:OFF_HASHPTR + 8], "little")
        hsize = int.from_bytes(ent[OFF_HASHSIZE:OFF_HASHSIZE + 4], "little")
        if hashptr and hsize:
            b = hash_guid(cle) & (hsize - 1)
            case = c.lire(hashptr + b * 4, 4)
            if case:
                c.ecrire(el + 4, b.to_bytes(4, "little"))
                c.ecrire(el, case)
                c.ecrire(hashptr + b * 4, num.to_bytes(4, "little"))
        c.ecrire(obj + OFF_NUM, (num + 1).to_bytes(4, "little"))
        log(f"  cle {nom} inseree (pas {pas})")
        return {"entete": el + off_val, "ptr": ptr, "num": len(pool), "cap": len(pool),
                "type": nom, "region": None, "pas": pas, "insere": True}
    return None


# ------------------------------------------------------------------ remplir --
def _tampon(pool, n):
    return b"".join(g(pool[i]) + uuid.uuid5(uuid.NAMESPACE_OID, pool[i]).bytes_le for i in range(n))


def dump(c, t):
    CACHE.mkdir(parents=True, exist_ok=True)
    f = CACHE / f"undo_{t['type']}_{datetime.now():%H%M%S_%f}.json"
    entete = c.lire(t["entete"], 16)
    contenu = c.lire(t["ptr"], t["num"] * PAS_ENT)
    f.write_text(json.dumps({"entete": t["entete"], "entete_hex": (entete or b"").hex(),
                             "ptr": t["ptr"], "contenu_hex": (contenu or b"").hex()}), encoding="utf-8")
    return f


def remplir(c, t, pool, profond=False):
    if t.get("insere"):
        return True
    if profond and not t.get("redirige"):
        libre = max(0, t["cap"] - t["num"])
        q = min(libre, len(pool))
        if q and c.ecrire(t["ptr"] + t["num"] * PAS_ENT, _tampon(pool, q)):
            if c.ecrire(t["entete"] + OFF_NUM, (t["num"] + q).to_bytes(4, "little")):
                t["num"] += q
                t["ecrit"] = q
        if t["num"] >= len(pool):
            return True
        total = len(pool)
        ptr = c.allouer(total * PAS_ENT)
        if not ptr:
            log(f"  {t['type']} : alloc impossible, reste a {t['num']}")
            return True
        if c.ecrire(ptr, _tampon(pool, total)):
            if c.ecrire(t["entete"], ptr.to_bytes(8, "little") + total.to_bytes(4, "little") * 2):
                t["ptr"], t["num"], t["cap"], t["redirige"] = ptr, total, total, True
        return True

    n = min(len(pool), t["cap"])
    if n <= 0:
        return False
    if n < len(pool):
        log(f"  {t['type']} : troncature {n}/{len(pool)} (capacite du tableau)")
    if not c.ecrire(t["ptr"], _tampon(pool, n)):
        return False
    if not c.ecrire(t["entete"] + OFF_NUM, n.to_bytes(4, "little")):
        return False
    t["num"] = n
    t["ecrit"] = n
    return c.lire(t["ptr"], 32) == _tampon(pool, 1)


# ------------------------------------------------------------------ loadout --
def _slot_ici(data, i, adresse):
    voisin = data[i + 16:i + 16 + 64]
    if len(voisin) < 64:
        return None
    if not (guid_ou_nil(voisin[0:16]) and guid_ou_nil(voisin[16:32]) and guid_ou_nil(voisin[32:48])):
        return None
    att = voisin[48:64]
    p8 = int.from_bytes(att[0:8], "little")
    n4 = int.from_bytes(att[8:12], "little")
    c4 = int.from_bytes(att[12:16], "little")
    if p8 == 0:
        if n4 or c4:
            return None
    elif p8 % 8 or n4 > c4 or c4 > 100000:
        return None
    return adresse + 16


def localiser_slot(c, slot_id):
    for m in motifs(slot_id):
        for base, taille in c.regions():
            res = _parcourir(c, base, taille, m,
                              lambda a: _slot_ici(c.lire(a, 80) or b"", 0, a))
            if res:
                return res
    return None


def localiser_zone(c, slot_id, lo, hi):
    if hi <= lo:
        return None
    data = c.lire(lo, hi - lo)
    if not data:
        return None
    for m in motifs(slot_id):
        i = data.find(m)
        while i != -1:
            a = _slot_ici(data, i, lo + i)
            if a:
                return a
            i = data.find(m, i + 1)
    return None


def indexer(c, ids):
    out, ancre = {}, None
    for sid in ids:
        a = localiser_slot(c, sid)
        if a:
            ancre = a
            out[sid] = a
            break
    if ancre is None:
        return {}
    marge = 0x100 + len(ids) * MARGE_SLOT
    lo, hi = max(0x10000, ancre - marge), ancre + marge
    for sid in ids:
        if sid not in out:
            a = localiser_zone(c, sid, lo, hi)
            if a:
                out[sid] = a
    return out


def carte_slots(cat):
    out = {}
    if BACKUP.exists():
        try:
            body = json.loads(BACKUP.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            body = {}
        for s in slots(body):
            nom = cat["skin_arme"].get(s.get("SkinID") or "")
            if nom:
                out[s["ID"]] = nom
    out.setdefault(MELEE, "Melee")
    return out


def equiper_mem(c, adresses, slot_id, skin, niveau, chroma):
    a = adresses.get(slot_id)
    if not a:
        return False
    if not c.ecrire(a, g(skin) + g(niveau) + g(chroma)):
        return False
    return c.lire(a, 16) == g(skin)


def etat(c, tableaux, adresses, voulu):
    for nom, t in tableaux.items():
        v = c.lire(t["ptr"], 16)
        log(f"  {nom:12} {'patch actif' if v and v[:16] != NUL16 else 'PERDU'}")
    for sid, (s, _, _) in voulu.items():
        a = adresses.get(sid)
        log(f"  {sid[:8]} {'skin en place' if a and c.lire(a, 16) == g(s) else 'PERDU'}")


def surveiller(c, tableaux, adresses, voulu, pools, ancres, noms, profond=False):
    log("\n  patch actif - Ctrl+C pour rendre la main")
    while True:
        time.sleep(1.5)
        if not pid_jeu():
            log("  jeu ferme")
            return
        for nom, t in list(tableaux.items()):
            v = c.lire(t["ptr"], 16)
            if v is not None and v[:16] != NUL16:
                continue
            res = None
            if t.get("region"):
                pool_jeu = {g(u) for u in (pools.get(nom) or [])[:3000]}
                for it in ancres.get(nom, [])[:3]:
                    res = localiser_region(c, it, pool_jeu, t["region"])
                    if res:
                        break
            if not res:
                res = localiser(c, nom, pools.get(nom) or [], ancres.get(nom, []))
            if not res:
                continue
            res["type"] = nom
            tableaux[nom] = res
            dump(c, res)
            remplir(c, res, pools.get(nom) or [], profond)
            log(f"  {nom} reapplique")
        perdu = [s for s, (sk, _, _) in voulu.items()
                 if not adresses.get(s) or c.lire(adresses[s], 16) != g(sk)]
        if perdu:
            neuf = indexer(c, list(noms))
            if neuf:
                adresses.clear()
                adresses.update(neuf)
                for sid in perdu:
                    sk, nv, ch = voulu[sid]
                    if adresses.get(sid):
                        c.ecrire(adresses[sid], g(sk) + g(nv) + g(ch))


# -------------------------------------------------------------------- main ---
def skins_de(cat, nom_arme):
    return cat["armes"].get(nom_arme) or []


def choisir_variante_auto(skin):
    niveau = skin["niveaux"][-1]
    chromas = niveau["chromas"] or [{"uuid": NIL}]
    return niveau["uuid"], random.choice(chromas)["uuid"]


def choisir_skin(skins, filtre=None):
    if filtre:
        skins = [s for s in skins if filtre.lower() in s["nom"].lower()]
    if not skins:
        log("  aucune skin pour ce filtre")
        return None
    for i, s in enumerate(skins, 1):
        log(f"  {i:3}. {s['nom']}")
    try:
        k = int(input("Skin > ")) - 1
    except (ValueError, EOFError):
        return None
    return skins[k] if 0 <= k < len(skins) else None


def choisir_variante(skin):
    niveau = skin["niveaux"][-1]
    if len(skin["niveaux"]) > 1:
        log("  niveau :")
        for i, n in enumerate(skin["niveaux"], 1):
            log(f"    {i}. {n['nom'] or 'niveau ' + str(i)}")
        try:
            k = int(input("Niveau (Entree = max) > ") or 0) - 1
            if 0 <= k < len(skin["niveaux"]):
                niveau = skin["niveaux"][k]
        except (ValueError, EOFError):
            pass
    chromas = niveau["chromas"] or [{"uuid": NIL, "nom": "defaut"}]
    if len(chromas) > 1:
        log("  variante :")
        for i, ch in enumerate(chromas, 1):
            log(f"    {i}. {ch['nom'] or 'variante ' + str(i)}")
        try:
            k = int(input("Variante (Entree = 1) > ") or 1) - 1
            if 0 <= k < len(chromas):
                return niveau["uuid"], chromas[k]["uuid"]
        except (ValueError, EOFError):
            pass
    return niveau["uuid"], chromas[0]["uuid"]


def main():
    global ORDRE
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    if "--stop" in sys.argv:
        arreter_jeu()
        return 0

    if not elever():
        log("  relance en administrateur...")
        return 0
    if not ctypes.windll.shell32.IsUserAnAdmin():
        raise Erreur("relance le terminal en administrateur")

    if "--fixdriver" in sys.argv:
        fixdriver()
        return 0

    if "--prep" in sys.argv:
        log("preparation : Vanguard coupe, driver charge")
        ok = preparer()
        log("  pret, relance sans --prep" if ok else "  driver toujours KO, suis la liste")
        return 0

    DRIVER = "--driver" in sys.argv
    if DRIVER:
        log("preparation du driver (Vanguard coupe, jeu relance)")
        if not preparer():
            log("  on continue sans driver : lectures probablement mortes")

    if "--undo" in sys.argv:
        c = Cible(pid_jeu(), "driver" if DRIVER else "user")
        try:
            n = 0
            for f in sorted(CACHE.glob("undo_*.json")):
                d = json.loads(f.read_text(encoding="utf-8"))
                if d["contenu_hex"]:
                    c.ecrire(d["ptr"], bytes.fromhex(d["contenu_hex"]))
                if d["entete_hex"]:
                    c.ecrire(d["entete"], bytes.fromhex(d["entete_hex"]))
                f.unlink()
                n += 1
            log(f"  {n} restauration(s)")
        finally:
            c.fermer()
        return 0

    cat = catalogue(force="--rebuild" in sys.argv)
    log(f"  catalogue : {len(cat['armes'])} armes, "
        f"{ {k: len(v) for k, v in cat['pools'].items() if v} }")

    log("connexion au client...")
    if not pid_jeu():
        lancer_jeu()
        fin = time.monotonic() + 300
        while not pid_jeu() and time.monotonic() < fin:
            time.sleep(2)
    ctx = contexte()
    sauver_loadout(ctx)

    if "--restore" in sys.argv:
        if not SAUVE.exists():
            raise Erreur("aucune sauvegarde de loadout")
        body = json.loads(SAUVE.read_text(encoding="utf-8"))
        voulu = {s["ID"]: (s["SkinID"], s["SkinLevelID"], s["ChromaID"])
                 for s in slots(body) if s.get("ID")}
        log(f"  {put_loadout(ctx, voulu)} arme(s) restauree(s)")
        return 0

    API_SEULE = "--api-only" in sys.argv
    blocs = blocs_entitlements(ctx)
    labels, ancres = etiqueter(blocs, cat)
    log(f"  types reels : { {k: len(v) for k, v in ancres.items()} }")
    log(f"  libelles reconnus : {sorted(set(labels.values())) or 'aucun'}")
    if not ancres:
        ancres = ancres_depuis_loadout(ctx)
        log(f"  ancres de repli (loadout) : { {k: len(v) for k, v in ancres.items()} }")
    if not ancres:
        log("  aucune ancre -> impossible de localiser quoi que ce soit")

    TYPES_VIVANTS = dict(TYPES)
    for t, nom in labels.items():
        TYPES_VIVANTS[nom] = t

    c = None
    if not API_SEULE:
        c = Cible(pid_jeu(), "driver" if DRIVER else "user")
        log(f"  acces memoire : {c.mode}")
        if c.mode == "user":
            _regs = c.regions()
            _sonde = None
            for _b, _t in _regs[:8]:
                _sonde = c.lire(_b, 16)
                if _sonde:
                    break
            _err = ctypes.get_last_error()
            log(f"  memoire : {len(_regs)} regions, "
                f"sonde {'ok' if _sonde else f'ECHEC (err {_err})'}")
            if not _sonde and not DRIVER:
                log("  lectures bloquees -> preparation du driver")
                if preparer():
                    c.fermer()
                    c = Cible(pid_jeu(), "driver")
                    log(f"  acces memoire : {c.mode}")
    try:
        if c:
            log(f"  ordre des GUID en memoire : {detecter_ordre(c, ancres)}")

        if "--diag" in sys.argv:
            if not c:
                raise Erreur("--diag exige l'acces memoire")
            diag(c, ctx, cat, ancres)
            return 0

        PROFOND = "--deep" in sys.argv
        SEC = "--dry" not in sys.argv and not API_SEULE
        log("  mode : " + ("PROFOND (insertion)" if PROFOND else "SUR (en place)"))

        if "--collection" in sys.argv or "--full" in sys.argv:
            demandes = list(TYPES)
        elif "--types" in sys.argv:
            demandes = sys.argv[sys.argv.index("--types") + 1].split(",")
        elif "--all" in sys.argv:
            demandes = list(TYPES)
        else:
            demandes = DEFAUT
        pools = {k: cat["pools"][k] for k in demandes if cat["pools"].get(k)}

        tableaux = {}
        if SEC and pools:
            log(f"  types : {', '.join(pools)}")
            for nom, pool in pools.items():
                if not ancres.get(nom):
                    if PROFOND:
                        t = inserer(c, nom, TYPES_VIVANTS.get(nom), pool, tableaux)
                        if t:
                            tableaux[nom] = t
                            log(f"  {nom} : insere ({len(pool)} entrees)")
                        else:
                            log(f"  {nom} : sans ancre, insertion impossible")
                    else:
                        log(f"  {nom} : sans ancre (--deep pour inserer)")
                    continue
                t = localiser(c, nom, pool, ancres[nom])
                if not t:
                    log(f"  {nom} : tableau introuvable")
                    continue
                tableaux[nom] = t
                dump(c, t)
                if not remplir(c, t, pool, False):
                    log(f"  {nom} : ecriture refusee")
                    tableaux.pop(nom, None)
                    continue
                log(f"  {nom} : {t.get('ecrit')} / {t['cap']} (pas {t['pas']})")
            if not tableaux:
                log("  rien de patchable -> diagnostic")
                diag(c, ctx, cat, ancres)

        noms = carte_slots(cat)
        ids = [s["ID"] for s in slots(ctx["loadout"]) if s.get("ID")] or list(noms)
        adresses = indexer(c, ids) if c else {}
        log(f"  loadout : {len(adresses)} arme(s) indexee(s)")

        voulu = {}

        def poser(sid, skin, nv, ch):
            voulu[sid] = (skin, nv, ch)
            if c and adresses.get(sid):
                equiper_mem(c, adresses, sid, skin, nv, ch)

        def equiper_arme(nom_arme, filtre=None, chroma=None, niveau=None):
            sid = next((s for s, n in noms.items() if n == nom_arme and s in ids), None)
            if not sid:
                log(f"  {nom_arme} : slot introuvable")
                return False
            skins = skins_de(cat, nom_arme)
            if not skins:
                log(f"  {nom_arme} : aucune skin")
                return False
            skin = choisir_skin(skins, filtre)
            if not skin:
                return False
            nv, ch = choisir_variante(skin)
            if niveau and 0 < niveau <= len(skin["niveaux"]):
                nv = skin["niveaux"][niveau - 1]["uuid"]
            if chroma:
                chromas = skin["niveaux"][-1]["chromas"]
                if 0 < chroma <= len(chromas):
                    ch = chromas[chroma - 1]["uuid"]
            poser(sid, skin["uuid"], nv, ch)
            log(f"  {nom_arme} : {skin['nom']} (mem{' + API' if c else ' / API'})")
            return True

        def tout_au_hasard():
            n = 0
            for sid, nom in noms.items():
                if sid not in ids:
                    continue
                skins = skins_de(cat, nom)
                if not skins:
                    continue
                sk = random.choice(skins)
                nv, ch = choisir_variante_auto(sk)
                poser(sid, sk["uuid"], nv, ch)
                n += 1
            log(f"  {n} arme(s) preparee(s)")

        if "--state" in sys.argv:
            if not c:
                raise Erreur("--state exige l'acces memoire")
            etat(c, tableaux, adresses, voulu)
            return 0

        if "--restore-loadout" in sys.argv and BACKUP.exists():
            body = json.loads(BACKUP.read_text(encoding="utf-8"))
            n = 0
            for s in slots(body):
                nom = cat["skin_arme"].get(s.get("SkinID") or "")
                sid = next((k for k, v in noms.items() if v == nom and k in ids), None)
                if sid:
                    poser(sid, s["SkinID"], s["SkinLevelID"], s["ChromaID"])
                    n += 1
            log(f"  {n} arme(s) restauree(s)")
        elif "--full" in sys.argv or "--all" in sys.argv:
            tout_au_hasard()
        elif "--vandal" in sys.argv or "--weapon" in sys.argv:
            nom = "Vandal" if "--vandal" in sys.argv else sys.argv[sys.argv.index("--weapon") + 1]
            cible = next((n for n in cat["armes"] if n.lower() == nom.lower()), nom)
            filtre = sys.argv[sys.argv.index("--skin") + 1] if "--skin" in sys.argv else None
            chroma = int(sys.argv[sys.argv.index("--chroma") + 1]) if "--chroma" in sys.argv else None
            niveau = int(sys.argv[sys.argv.index("--level") + 1]) if "--level" in sys.argv else None
            equiper_arme(cible, filtre, chroma, niveau)

        if voulu:
            n = put_loadout(ctx, voulu)
            log(f"  loadout PUT : {n}/{len(voulu)} skin(s) confirmes par le serveur")

        if "--nowatch" in sys.argv or API_SEULE:
            return 0
        if "--full" in sys.argv:
            if c and tableaux:
                surveiller(c, tableaux, adresses, voulu, pools, ancres, ids, PROFOND)
            return 0

        while True:
            log("\n  1. tout au hasard\n  2. choisir une arme\n  3. etat\n  4. collection seule\n  0. quitter")
            rep = input("> ").strip()
            if rep == "1":
                tout_au_hasard()
                log(f"  {put_loadout(ctx, voulu)}/{len(voulu)} confirmes")
            elif rep == "2":
                liste = sorted(((noms.get(i, i), i) for i in ids), key=lambda x: x[0])
                for i, (n, _) in enumerate(liste, 1):
                    log(f"  {i:2}. {n}")
                try:
                    nom = liste[int(input("Arme > ")) - 1][0]
                except (ValueError, IndexError, EOFError):
                    continue
                equiper_arme(nom)
                log(f"  {put_loadout(ctx, voulu)}/{len(voulu)} confirmes")
            elif rep == "3":
                etat(c, tableaux, adresses, voulu)
            elif rep == "4":
                for nom, pool in {k: cat["pools"][k] for k in TYPES if cat["pools"].get(k)}.items():
                    t = localiser(c, nom, pool, ancres.get(nom, []))
                    if t:
                        dump(c, t)
                        remplir(c, t, pool, False)
                        log(f"  {nom} : {t.get('ecrit')}/{t['cap']}")
            elif rep == "0":
                break

        if c and tableaux:
            surveiller(c, tableaux, adresses, voulu, pools, ancres, ids, PROFOND)
    finally:
        if c:
            c.fermer()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Erreur as e:
        log(f"\n{e}")
        raise SystemExit(1)
    except KeyboardInterrupt:
        log("\nstop")