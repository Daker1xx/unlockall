#!/usr/bin/env python3
"""
unlockall.py - unlockall client-side VALORANT (ancrage sur les entitlements reels).

  python unlockall.py                 menu
  python unlockall.py --all           equipement aleatoire complet + patch
  python unlockall.py --weapon vandal  choisir une skin sur une arme
  python unlockall.py --state         etat du patch
  python unlockall.py --undo          restaure tout
  python unlockall.py --rebuild       reconstruit le catalogue
  python unlockall.py --scan          diagnostic memoire -> cache/diag.txt
  python unlockall.py --deep          insertion des types absents
  python unlockall.py --nowatch       applique puis rend la main
  python unlockall.py --stop          ferme VALORANT et Riot Client
"""
from __future__ import annotations

import base64, ctypes, json, os, random, subprocess, sys, time, uuid
from ctypes import wintypes
from datetime import datetime
from pathlib import Path

try:
    import requests, urllib3
except ImportError:
    raise SystemExit("pip install requests")
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

ROOT = Path(__file__).resolve().parent
CACHE = ROOT / "cache"
CATALOGUE_FILE = CACHE / "catalog.json"
DUMP_DIR = CACHE
LOCALAPPDATA = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
LOCKFILE = LOCALAPPDATA / "Riot Games" / "Riot Client" / "Config" / "lockfile"
RIOT = [Path(r"C:\Riot Games\Riot Client\RiotClientServices.exe"),
        Path(r"C:\Program Files\Riot Games\Riot Client\RiotClientServices.exe")]

API = "https://valorant-api.com/v1"
PROC = ["VALORANT-Win64-Shipping.exe", "VALORANT.exe"]

CATALOGUE_VERSION = 7

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

VM_R, VM_W, VM_O, QI = 0x10, 0x20, 0x8, 0x400
MEM_COMMIT, MEM_PRIVATE, MEM_MAPPED, MEM_RESERVE = 0x1000, 0x20000, 0x40000, 0x2000
PAGE_READWRITE = 0x04
PROTS = {0x02, 0x04, 0x08, 0x40, 0x80}
CHUNK = 4 * 1024 * 1024
PLAFOND = 0x7FFFFFFFFFFF
MAX_ITEMS = 20000
MAX_ARRAY = 20000
PAS_ENT = 32
PAS_ELEMS = (40, 48)
OFF_NUM, OFF_MAX = 8, 12
OFF_FLAGS, OFF_HASHPTR, OFF_HASHSIZE = 0x10, 0x30, 0x40
MARGE_SLOT = 0x60

ORDRE = "le"

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


class Erreur(RuntimeError):
    pass


def say(m=""):
    print(m, flush=True)


# ------------------------------------------------------------------ memoire --
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


def ouvrir(tentatives=15):
    if not ctypes.windll.shell32.IsUserAnAdmin():
        raise Erreur("relance en administrateur")
    pid = pid_jeu()
    if not pid:
        raise Erreur("jeu introuvable")
    dernier = 0
    for _ in range(tentatives):
        for droits in (VM_R | VM_W | VM_O | QI, VM_R | VM_O | QI, VM_R | QI):
            h = k32.OpenProcess(droits, False, pid)
            if h:
                return h
            dernier = ctypes.get_last_error()
        time.sleep(1.0)
    raise Erreur(f"OpenProcess refuse ({dernier}) - lance le jeu puis relance le script")


def regions(h):
    out, mbi = [], MBI()
    a = 0x10000
    while a < PLAFOND:
        if not k32.VirtualQueryEx(h, ctypes.c_void_p(a), ctypes.byref(mbi), ctypes.sizeof(mbi)):
            break
        b, t = mbi.BaseAddress, mbi.RegionSize
        if mbi.State == MEM_COMMIT and mbi.Type in (MEM_PRIVATE, MEM_MAPPED) \
                and mbi.Protect in PROTS and t:
            out.append((b, t))
        if not t:
            break
        a = b + t
    return out


def lire(h, a, n):
    if n <= 0:
        return b""
    buf = ctypes.create_string_buffer(n)
    lus = ctypes.c_size_t()
    if not k32.ReadProcessMemory(h, ctypes.c_void_p(a), buf, n, ctypes.byref(lus)) or lus.value != n:
        return None
    return buf.raw


def ecrire(h, a, octets):
    buf = ctypes.create_string_buffer(octets, len(octets))
    f = ctypes.c_size_t()
    return bool(k32.WriteProcessMemory(h, ctypes.c_void_p(a), buf, len(octets), ctypes.byref(f))) \
        and f.value == len(octets)


def g(u):
    uu = uuid.UUID(u)
    return uu.bytes_le if ORDRE == "le" else uu.bytes


def motifs(u):
    uu = uuid.UUID(u)
    return (uu.bytes_le,) if uu.bytes_le == uu.bytes else (uu.bytes_le, uu.bytes)


def guid_valide(b):
    if b is None or len(b) != 16 or b == b"\x00" * 16:
        return False
    for conv in (uuid.UUID,):
        for kw in ("bytes_le", "bytes"):
            try:
                u = conv(**{kw: b})
            except ValueError:
                continue
            if u.variant == uuid.RFC_4122 and u.version in (3, 4, 5):
                return True
    return False


def guid_ou_nil(b):
    return b is not None and len(b) == 16 and (b == b"\x00" * 16 or guid_valide(b))


_TAB = []
for i in range(256):
    c = i
    for _ in range(8):
        c = (c >> 1) ^ 0xEDB88320 if c & 1 else c >> 1
    _TAB.append(c)


def hash_guid(b):
    c = 0
    for x in b:
        c = (c >> 8) ^ _TAB[(c ^ x) & 0xFF]
    return c


# --------------------------------------------------------------------- API ---
def version_client(base, loc):
    try:
        s = requests.get(f"{base}/product-session/v1/external-sessions", headers=loc,
                         verify=False, timeout=5).json()
        for v in s.values():
            if v.get("productId") == "valorant" and v.get("version"):
                return v["version"]
    except Exception:
        pass
    try:
        return requests.get(f"{API}/version", timeout=10).json()["data"]["riotClientVersion"]
    except Exception:
        return ""


def contexte(timeout=600.0):
    SESS = requests.Session()
    SESS.trust_env = False
    SESS.verify = False
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
            r = SESS.get(f"{base}/entitlements/v1/token", headers=loc, timeout=15)
            if r.status_code != 200:
                raise Erreur(f"token -> HTTP {r.status_code} {r.text[:80]}")
            ent = r.json()
            reg = SESS.get(f"{base}/riotclient/region-locale", headers=loc, timeout=15).json()
            shard = reg.get("shard") or SHARD.get((reg.get("region") or "").lower())
            if not shard:
                raise Erreur(f"shard introuvable : {reg}")
            pd = f"https://pd.{shard}.a.pvp.net"
            entetes = {"Authorization": f"Bearer {ent['accessToken']}",
                       "X-Riot-Entitlements-JWT": ent["token"],
                       "X-Riot-ClientPlatform": PLATFORM, "Content-Type": "application/json"}
            ver = version_client(base, loc)
            say(f"  version client : {ver or 'INCONNUE'}")
            if ver:
                entetes["X-Riot-ClientVersion"] = ver
            puuid = ent["subject"]
            url = f"{pd}/personalization/v3/players/{puuid}/playerloadout"
            r2 = SESS.get(url, headers=entetes, timeout=20)
            if r2.status_code != 200:
                raise Erreur(f"loadout -> HTTP {r2.status_code} {r2.text[:120]}")
            return {"pd": pd, "entetes": entetes, "puuid": puuid, "loadout": r2.json()}
        except Exception as e:
            m = f"{type(e).__name__}: {e}"
            if m != dernier:
                say(f"  -> {m}")
                dernier = m
        time.sleep(3.0)
    raise Erreur("API du client injoignable")


def blocs_entitlements(ctx):
    try:
        r = requests.get(f"{ctx['pd']}/store/v1/entitlements/{ctx['puuid']}",
                         headers=ctx["entetes"], verify=False, timeout=20)
        return (r.json() or {}).get("EntitlementsByTypes") or []
    except (requests.RequestException, ValueError):
        return []


# --------------------------------------------------------------- catalogue ---
def _pool_propre(v):
    v = [u for u in (v or []) if isinstance(u, str) and u and u != NIL]
    return list(dict.fromkeys(v))


def _normaliser(brut):
    pools = {k: [] for k in TYPES}
    armes, armes_id = {}, {}

    if isinstance(brut.get("pools"), dict):
        for k, v in brut["pools"].items():
            pools.setdefault(k, []).extend(v or [])
        armes = {k: v for k, v in (brut.get("armes") or {}).items() if v}
        armes_id = {k: v for k, v in (brut.get("armes_id") or brut.get("uuid_armes") or {}).items() if v}
    else:
        armes_id = {k: v for k, v in (brut.get("uuid_armes") or {}).items() if v}
        for s in brut.get("skins") or []:
            if not s.get("uuid") or s["uuid"] == NIL:
                continue
            nom = s.get("weapon") or s.get("arme") or "?"
            if s.get("weapon_uuid") or s.get("arme_uuid"):
                armes_id[s.get("weapon_uuid") or s.get("arme_uuid")] = nom
            skin_chromas = [c.get("uuid") for c in (s.get("chromas") or [])
                            if isinstance(c, dict) and c.get("uuid") and c["uuid"] != NIL]
            niveaux = []
            for n in s.get("levels") or s.get("niveaux") or []:
                if not n.get("uuid") or n["uuid"] == NIL:
                    continue
                chromas = [c.get("uuid") for c in (n.get("chromas") or [])
                           if isinstance(c, dict) and c.get("uuid") and c["uuid"] != NIL]
                if not chromas:
                    chromas = list(skin_chromas)
                niveaux.append({"uuid": n["uuid"], "chromas": chromas})
                pools["levels"].append(n["uuid"])
                pools["chromas"] += chromas
            if not niveaux:
                continue
            pools["skins"].append(s["uuid"])
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
    return {"version": CATALOGUE_VERSION, "armes": armes, "armes_id": armes_id, "pools": pools}


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
            skin_chromas = [c["uuid"] for c in s.get("chromas") or [] if c["uuid"] != NIL]
            niveaux = []
            for n in s["levels"]:
                if not n.get("uuid") or n["uuid"] == NIL:
                    continue
                chromas = [c["uuid"] for c in n.get("chromas") or [] if c["uuid"] != NIL]
                if not chromas:
                    chromas = list(skin_chromas)
                niveaux.append({"uuid": n["uuid"], "chromas": chromas})
                pools["levels"].append(n["uuid"])
                pools["chromas"] += chromas
            if not niveaux:
                continue
            pools["skins"].append(s["uuid"])
            skins.append({"uuid": s["uuid"], "nom": s["displayName"], "niveaux": niveaux})
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
            say(f"  {route} : {e}")

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
            say("  cache dans un format ancien -> normalisation")
            cat = _normaliser(brut)
            if cat["armes"] and cat["pools"].get("chromas"):
                _ecrire_cache(cat)
                return cat
            say("  cache inexploitable -> reconstruction")
        except (OSError, ValueError) as e:
            say(f"  cache illisible ({e}) -> reconstruction")

    try:
        cat = _construire()
    except (requests.RequestException, ValueError) as e:
        raise Erreur(f"catalogue indisponible : {e}")
    if not cat["armes"]:
        raise Erreur("catalogue vide")
    _ecrire_cache(cat)
    return cat


def etiqueter(blocs, cat):
    """Associe chaque type reel a un libelle en croisant avec les pools du catalogue."""
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
def carte_depuis(h, entete, pas):
    for k in range(1, 600):
        obj = entete - 24 - k * pas
        ent = lire(h, obj, 0x44)
        if not ent:
            continue
        data = int.from_bytes(ent[0:8], "little")
        num = int.from_bytes(ent[OFF_NUM:OFF_NUM + 4], "little")
        mx = int.from_bytes(ent[OFF_MAX:OFF_MAX + 4], "little")
        if data and data % 16 == 0 and num < mx < 8192 and data <= entete \
                and (entete - 24 - data) % pas == 0:
            return obj, data, num, mx, ent
    return None


def _paire(h, a):
    b = lire(h, a, 32)
    return b if (b and guid_valide(b[0:16]) and guid_valide(b[16:32])) else None


def _depuis_valeur(h, a, pool_jeu, region):
    s = a
    for _ in range(MAX_ARRAY):
        if not _paire(h, s - 32):
            break
        s -= 32
    e = a + 32
    for _ in range(MAX_ARRAY):
        if not _paire(h, e):
            break
        e += 32
    entete = s - 16
    hd = lire(h, entete, 16)
    if not hd:
        return None
    ptr = int.from_bytes(hd[0:8], "little")
    num = int.from_bytes(hd[8:12], "little")
    cap = int.from_bytes(hd[12:16], "little")
    total = (e - s) // 32
    if ptr != s or total < 2 or num < total or cap < num or cap > 400000:
        return None
    pas, cle = None, None
    for cand, recul in ((40, 24), (48, 32)):
        kv = lire(h, entete - recul, 16)
        if kv and guid_valide(kv):
            pas, cle = cand, kv
            break
    if pas is None:
        return None
    if not carte_depuis(h, entete, pas):
        return None
    contenu = lire(h, s, min(total, 400) * PAS_ENT)
    if not contenu:
        return None
    score = 0
    for j in range(len(contenu) // 32):
        if contenu[j * 32:j * 32 + 16] in pool_jeu:
            score += 2
    return {"entete": entete, "ptr": s, "num": num, "cap": cap, "pas": pas,
            "cle": cle.hex(), "region": region, "score": score}


def localiser_valeur(h, itemid, pool_jeu):
    for m in motifs(itemid):
        for base, taille in regions(h):
            pos = 0
            while pos < taille:
                bloc = min(CHUNK, taille - pos)
                data = lire(h, base + pos, bloc)
                if data is None:
                    break
                i = data.find(m)
                while i != -1:
                    c = _depuis_valeur(h, base + pos + i, pool_jeu, (base, taille))
                    if c:
                        return c
                    i = data.find(m, i + 1)
                pos += max(1, bloc - 15)
    return None


def localiser_region(h, itemid, pool_jeu, region):
    base, taille = region
    for m in motifs(itemid):
        pos = 0
        while pos < taille:
            bloc = min(CHUNK, taille - pos)
            data = lire(h, base + pos, bloc)
            if data is None:
                return None
            i = data.find(m)
            while i != -1:
                c = _depuis_valeur(h, base + pos + i, pool_jeu, region)
                if c:
                    return c
                i = data.find(m, i + 1)
            pos += max(1, bloc - 15)
    return None


def localiser(h, nom, pool, ancres):
    pool_jeu = {g(u) for u in pool[:3000]}
    meilleur = None
    for it in ancres[:6]:
        c = localiser_valeur(h, it, pool_jeu)
        if c and (meilleur is None or c["score"] > meilleur["score"]):
            c["type"] = nom
            meilleur = c
    return meilleur


def detecter_ordre(h, ancres):
    global ORDRE
    temoin = next((a for l in ancres.values() for a in l[:1]), None)
    if not temoin:
        return "le"
    m = uuid.UUID(temoin).bytes_le
    for base, taille in regions(h):
        pos = 0
        while pos < taille:
            bloc = min(CHUNK, taille - pos)
            d = lire(h, base + pos, bloc)
            if d is None:
                break
            if d.find(m) != -1:
                ORDRE = "le"
                return "le"
            pos += max(1, bloc - 15)
    ORDRE = "be"
    return "be"


# ------------------------------------------------------------------- scan ----
MARQUEURS = [b"EquippableSkin", b"ItemTypeID", b"Entitlement", b"VALORANT"]


def _compter(regs, motif, h, compteurs):
    n = 0
    for base, taille in regs:
        pos = 0
        while pos < taille:
            bloc = min(CHUNK, taille - pos)
            data = lire(h, base + pos, bloc)
            if data is None:
                compteurs["ko"] += 1
                compteurs["err"] = ctypes.get_last_error()
                break
            compteurs["ok"] += 1
            i = data.find(motif)
            while i != -1:
                n += 1
                i = data.find(motif, i + 1)
            pos += max(1, bloc - 15)
    return n


def diag(h, ctx, cat, ancres):
    regs = regions(h)
    total = sum(t for _, t in regs)
    c = {"ok": 0, "ko": 0, "err": 0}
    lignes = [f"regions={len(regs)} octets={total}", f"ordre={ORDRE}"]

    for m in MARQUEURS:
        n = _compter(regs, m, h, c)
        lignes.append(f"ascii {m.decode():16} : {n}")
        say(f"  ascii {m.decode():16} : {n}")

    for nom, ids in ancres.items():
        pool_jeu = {g(u) for u in (cat["pools"].get(nom) or [])[:3000]}
        c2 = localiser_valeur(h, ids[0], pool_jeu) if pool_jeu else None
        if c2:
            lignes.append(f"{nom:12} TROUVE ptr={c2['ptr']:X} num={c2['num']} cap={c2['cap']} "
                          f"pas={c2['pas']} cle={c2['cle']} score={c2['score']}")
            say(f"  {nom:12} trouve num={c2['num']} cap={c2['cap']} cle={c2['cle'][:16]}…")
        else:
            lignes.append(f"{nom:12} non localise")
            say(f"  {nom:12} non localise")

    lignes.append(f"lectures ok={c['ok']} ko={c['ko']} err={c['err']}")
    CACHE.mkdir(parents=True, exist_ok=True)
    (CACHE / "diag.txt").write_text("\n".join(lignes), encoding="utf-8")
    say(f"  diagnostic -> {CACHE / 'diag.txt'}")


# --------------------------------------------------------- insertion de cle --
def _cle_presente(h, carte, cle, pas):
    obj, data, num, mx, ent = carte
    for i in range(min(num, 128)):
        if lire(h, data + i * pas + 8, 16) == cle:
            return True
    return False


def inserer(h, nom, guid_type, pool, tableaux):
    if not guid_type:
        return None
    cle = g(guid_type)
    sources = [t for t in tableaux.values() if not t.get("insere")]
    for c in sources:
        pas = c["pas"]
        carte = carte_depuis(h, c["entete"], pas)
        if not carte or _cle_presente(h, carte, cle, pas):
            continue
        obj, data, num, mx, ent = carte
        if num >= mx:
            continue
        off_val = 24 if pas == 40 else 32
        i = num
        el = data + i * pas
        ptr = k32.VirtualAllocEx(h, None, len(pool) * PAS_ENT,
                                 MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE)
        if not ptr:
            return None
        tampon = b"".join(g(u) + uuid.uuid5(uuid.NAMESPACE_OID, u).bytes_le for u in pool)
        if not ecrire(h, ptr, tampon) or not ecrire(h, el + 8, cle):
            return None
        if not ecrire(h, el + off_val, ptr.to_bytes(8, "little")
                      + len(pool).to_bytes(4, "little") * 2):
            return None
        flags = int.from_bytes(ent[OFF_FLAGS:OFF_FLAGS + 8], "little")
        if flags:
            mot = flags + (i >> 5) * 4
            v = lire(h, mot, 4)
            if v:
                ecrire(h, mot, (int.from_bytes(v, "little") | (1 << (i & 31))).to_bytes(4, "little"))
        hashptr = int.from_bytes(ent[OFF_HASHPTR:OFF_HASHPTR + 8], "little")
        hsize = int.from_bytes(ent[OFF_HASHSIZE:OFF_HASHSIZE + 4], "little")
        if hashptr and hsize:
            b = hash_guid(cle) & (hsize - 1)
            case = lire(h, hashptr + b * 4, 4)
            if case:
                ecrire(h, el + 4, b.to_bytes(4, "little"))
                ecrire(h, el, case)
                ecrire(h, hashptr + b * 4, i.to_bytes(4, "little"))
        ecrire(h, obj + OFF_NUM, (i + 1).to_bytes(4, "little"))
        say(f"  cle {nom} inseree (pas {pas})")
        return {"entete": el + off_val, "ptr": ptr, "num": len(pool), "cap": len(pool),
                "type": nom, "region": None, "pas": pas, "insere": True}
    return None


# ------------------------------------------------------------------ remplir --
def dump(h, t):
    DUMP_DIR.mkdir(parents=True, exist_ok=True)
    f = DUMP_DIR / f"undo_{t['type']}_{datetime.now():%H%M%S_%f}.json"
    entete = lire(h, t["entete"], 16)
    contenu = lire(h, t["ptr"], t["num"] * PAS_ENT)
    f.write_text(json.dumps({"entete": t["entete"], "entete_hex": (entete or b"").hex(),
                             "ptr": t["ptr"], "contenu_hex": (contenu or b"").hex()}),
                 encoding="utf-8")
    return f


def remplir(h, t, pool, profond=False):
    if t.get("insere"):
        return True

    if profond:
        libre = max(0, t["cap"] - t["num"])
        q = min(libre, len(pool))
        if q:
            tampon = b"".join(g(pool[i]) + uuid.uuid5(uuid.NAMESPACE_OID, pool[i]).bytes_le
                              for i in range(q))
            if not ecrire(h, t["ptr"] + t["num"] * PAS_ENT, tampon):
                return False
            if not ecrire(h, t["entete"] + OFF_NUM, (t["num"] + q).to_bytes(4, "little")):
                return False
            t["num"] += q
            t["ecrit"] = q
        if t["num"] >= len(pool):
            return True
        total = len(pool)
        ptr = k32.VirtualAllocEx(h, None, total * PAS_ENT, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE)
        if not ptr:
            return True
        tampon = b"".join(g(u) + uuid.uuid5(uuid.NAMESPACE_OID, u).bytes_le for u in pool)
        if ecrire(h, ptr, tampon):
            ent = ptr.to_bytes(8, "little") + total.to_bytes(4, "little") * 2
            if ecrire(h, t["entete"], ent):
                t["ptr"], t["num"], t["cap"], t["redirige"] = ptr, total, total, True
        return True

    n = min(len(pool), t["cap"])
    if n <= 0:
        return False
    tampon = b"".join(g(pool[i]) + uuid.uuid5(uuid.NAMESPACE_OID, pool[i]).bytes_le
                      for i in range(n))
    if not ecrire(h, t["ptr"], tampon):
        return False
    if not ecrire(h, t["entete"] + OFF_NUM, n.to_bytes(4, "little")):
        return False
    t["num"] = n
    t["ecrit"] = n
    return True


# ------------------------------------------------------------------ loadout --
def slots(body):
    out = [s for s in (body.get("Guns") or []) if isinstance(s, dict)]
    if isinstance(body.get("Melee"), dict):
        out.append(body["Melee"])
    return out


def _slot_ici(data, base, i, adresse):
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


def localiser_slot(h, slot):
    for m in motifs(slot["ID"]):
        for base, taille in regions(h):
            pos = 0
            while pos < taille:
                bloc = min(CHUNK, taille - pos)
                data = lire(h, base + pos, bloc)
                if data is None:
                    break
                i = data.find(m)
                while i != -1:
                    a = _slot_ici(data, base + pos, i, base + pos + i)
                    if a:
                        return a
                    i = data.find(m, i + 1)
                pos += max(1, bloc - 79)
    return None


def localiser_zone(h, slot, lo, hi):
    if hi <= lo:
        return None
    data = lire(h, lo, hi - lo)
    if not data:
        return None
    for m in motifs(slot["ID"]):
        i = data.find(m)
        while i != -1:
            a = _slot_ici(data, lo, i, lo + i)
            if a:
                return a
            i = data.find(m, i + 1)
    return None


def indexer(h, body):
    ss = slots(body)
    out, ancre = {}, None
    for s in ss:
        if not s.get("ID"):
            continue
        a = localiser_slot(h, s)
        if a:
            ancre = a
            out[s["ID"]] = a
            break
    if ancre is None:
        return {}
    marge = 0x100 + len(ss) * MARGE_SLOT
    lo, hi = max(0x10000, ancre - marge), ancre + marge
    for s in ss:
        if s.get("ID") and s["ID"] not in out:
            a = localiser_zone(h, s, lo, hi)
            if a:
                out[s["ID"]] = a
    return out


def equip(h, corps, arme_id, skin, niveau=None, chroma=None):
    niveau = niveau or skin["niveaux"][-1]["uuid"]
    chroma = chroma or (skin["niveaux"][-1]["chromas"] or [NIL])[0]
    adresse = corps["adresses"].get(arme_id)
    if not adresse:
        return False
    ok = ecrire(h, adresse, g(skin["uuid"]) + g(niveau) + g(chroma))
    corps["voulu"][arme_id] = (skin["uuid"], niveau, chroma)
    return ok


def etat(h, tableaux, corps):
    for nom, t in tableaux.items():
        v = lire(h, t["ptr"], 16)
        ok = v is not None and v[:16] != b"\x00" * 16
        say(f"  {nom:12} {'patch actif' if ok else 'PERDU'}")
    for arme, (s, _, _) in corps["voulu"].items():
        a = corps["adresses"].get(arme)
        actuel = lire(h, a, 16) if a else None
        say(f"  {arme[:8]} {'skin en place' if actuel == g(s) else 'PERDU'}")


def surveiller(h, tableaux, corps, pools, ancres, profond=False):
    say("\n  patch actif - Ctrl+C pour rendre la main")
    while True:
        time.sleep(1.5)
        if not pid_jeu():
            say("  jeu ferme")
            return
        for nom, t in list(tableaux.items()):
            v = lire(h, t["ptr"], 16)
            if v is not None and v[:16] != b"\x00" * 16:
                continue
            c = None
            if t.get("region"):
                pool_jeu = {g(u) for u in pools[nom][:3000]}
                for it in ancres.get(nom, [])[:3]:
                    c = localiser_region(h, it, pool_jeu, t["region"])
                    if c:
                        break
            if not c:
                c = localiser(h, nom, pools[nom], ancres.get(nom, []))
            if not c:
                continue
            c["type"] = nom
            tableaux[nom] = c
            dump(h, c)
            remplir(h, c, pools[nom], profond)
            say(f"  {nom} reapplique")
        perdu = [a for a, (s, n, c) in corps["voulu"].items()
                 if not corps["adresses"].get(a)
                 or lire(h, corps["adresses"][a], 16) != g(s)]
        if perdu:
            neuf = indexer(h, corps["serveur"])
            if neuf:
                corps["adresses"] = neuf
                for a in perdu:
                    s, n, c = corps["voulu"][a]
                    if corps["adresses"].get(a):
                        ecrire(h, corps["adresses"][a], g(s) + g(n) + g(c))


# ------------------------------------------------------------------- main ----
def lancer_jeu():
    for exe in RIOT:
        if exe.exists():
            subprocess.Popen([str(exe), "--launch-product=valorant", "--launch-patchline=live"])
            return True
    return False


def arreter_jeu():
    for n in PROC + ["RiotClientServices.exe"]:
        subprocess.run(["taskkill", "/F", "/IM", n], capture_output=True, timeout=20)
    say("  jeu ferme")


def skins_de(cat, arme_id):
    return cat["armes"].get(cat["armes_id"].get(arme_id, "")) or []


def choisir(skins, filtre=None):
    if filtre:
        skins = [s for s in skins if filtre.lower() in s["nom"].lower()]
    if not skins:
        say("  aucune skin pour ce filtre")
        return None
    for i, s in enumerate(skins[:60], 1):
        say(f"  {i:3}. {s['nom']}")
    try:
        c = int(input("Skin > ")) - 1
    except (ValueError, EOFError):
        return None
    return skins[c] if 0 <= c < min(len(skins), 60) else None


def main():
    global ORDRE
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    if "--stop" in sys.argv:
        arreter_jeu()
        return 0

    if not ctypes.windll.shell32.IsUserAnAdmin():
        raise Erreur("relance le terminal en administrateur")

    if "--undo" in sys.argv:
        h = ouvrir()
        try:
            n = 0
            for f in sorted(DUMP_DIR.glob("undo_*.json")):
                d = json.loads(f.read_text(encoding="utf-8"))
                if d["contenu_hex"]:
                    ecrire(h, d["ptr"], bytes.fromhex(d["contenu_hex"]))
                if d["entete_hex"]:
                    ecrire(h, d["entete"], bytes.fromhex(d["entete_hex"]))
                f.unlink()
                n += 1
            say(f"  {n} restauration(s)")
        finally:
            k32.CloseHandle(h)
        return 0

    cat = catalogue(force="--rebuild" in sys.argv)
    say(f"  catalogue : {len(cat['armes'])} armes, "
        f"{ {k: len(v) for k, v in cat['pools'].items() if v} }")

    say("connexion au client…")
    if not pid_jeu():
        lancer_jeu()
        fin = time.monotonic() + 300
        while not pid_jeu() and time.monotonic() < fin:
            time.sleep(2)
    ctx = contexte()

    blocs = blocs_entitlements(ctx)
    labels, ancres = etiqueter(blocs, cat)
    say(f"  types reels : { {k: len(v) for k, v in ancres.items()} }")
    say(f"  libelles reconnus : {sorted(set(labels.values())) or 'aucun'}")

    TYPES_VIVANTS = dict(TYPES)
    for t, nom in labels.items():
        TYPES_VIVANTS[nom] = t

    h = ouvrir()
    try:
        ordre = detecter_ordre(h, ancres)
        say(f"  ordre des GUID en memoire : {ordre}")

        if "--scan" in sys.argv or "--diag" in sys.argv:
            diag(h, ctx, cat, ancres)
            return 0

        PROFOND = "--deep" in sys.argv
        say("  mode : " + ("PROFOND (insertion)" if PROFOND else "SUR (en place)"))

        demandes = sys.argv[sys.argv.index("--types") + 1].split(",") if "--types" in sys.argv else DEFAUT
        if "--all" in sys.argv:
            demandes = list(TYPES)
        pools = {k: cat["pools"][k] for k in demandes if cat["pools"].get(k)}
        if not pools:
            raise Erreur("aucun type exploitable")
        say(f"  types : {', '.join(pools)}")

        tableaux = {}
        for nom, pool in pools.items():
            if not ancres.get(nom):
                if PROFOND:
                    t = inserer(h, nom, TYPES_VIVANTS.get(nom), pool, tableaux)
                    if t:
                        tableaux[nom] = t
                        say(f"  {nom} : insere ({len(pool)} entrees)")
                    else:
                        say(f"  {nom} : non possede, insertion impossible")
                else:
                    say(f"  {nom} : non possede (--deep pour insérer)")
                continue
            t = localiser(h, nom, pool, ancres[nom])
            if not t:
                say(f"  {nom} : tableau introuvable")
                continue
            dump(h, t)
            if not remplir(h, t, pool, False):
                say(f"  {nom} : ecriture refusee")
                continue
            tableaux[nom] = t
            say(f"  {nom} : {t.get('ecrit')} / {t['cap']} (num {t['num']}, pas {t['pas']})")
        if not tableaux:
            say("  rien de patchable -> diagnostic")
            diag(h, ctx, cat, ancres)
            raise Erreur("rien localise")

        corps = {"serveur": ctx["loadout"], "adresses": {}, "voulu": {}}
        corps["adresses"] = indexer(h, corps["serveur"])
        if not corps["adresses"]:
            raise Erreur("structure de loadout introuvable")
        say(f"  loadout : {len(corps['adresses'])} arme(s) indexee(s)")

        if "--state" in sys.argv:
            etat(h, tableaux, corps)
            return 0
        if "--dry" in sys.argv:
            for arme_id in corps["adresses"]:
                say(f"    {cat['armes_id'].get(arme_id, arme_id)} : {len(skins_de(cat, arme_id))} skins")
            return 0

        def tout_aleatoire():
            n = 0
            for arme_id in corps["adresses"]:
                skins = skins_de(cat, arme_id)
                if skins and equip(h, corps, arme_id, random.choice(skins)):
                    n += 1
            say(f"  {n} arme(s) equipee(s)")

        if "--all" in sys.argv:
            tout_aleatoire()
        elif "--weapon" in sys.argv:
            nom = sys.argv[sys.argv.index("--weapon") + 1].lower()
            arme_id = next((i for i, n in cat["armes_id"].items()
                            if nom in n.lower() and i in corps["adresses"]), None)
            if not arme_id:
                raise Erreur("arme inconnue ou non verrouillee")
            s = choisir(skins_de(cat, arme_id),
                        sys.argv[sys.argv.index("--skin") + 1] if "--skin" in sys.argv else None)
            if s and equip(h, corps, arme_id, s):
                say(f"  {cat['armes_id'][arme_id]} : {s['nom']}")
        else:
            while True:
                say("\n  1. tout au hasard\n  2. choisir une arme\n  3. etat\n  0. quitter")
                c = input("> ").strip()
                if c == "1":
                    tout_aleatoire()
                elif c == "2":
                    liste = sorted(((cat["armes_id"].get(i, i), i) for i in corps["adresses"]),
                                   key=lambda x: x[0])
                    for i, (n, _) in enumerate(liste, 1):
                        say(f"  {i:2}. {n}")
                    try:
                        n, arme_id = liste[int(input("Arme > ")) - 1]
                    except (ValueError, IndexError, EOFError):
                        continue
                    s = choisir(skins_de(cat, arme_id))
                    if s and equip(h, corps, arme_id, s):
                        say(f"  {n} : {s['nom']}")
                elif c == "3":
                    etat(h, tableaux, corps)
                elif c == "0":
                    return 0

        if "--nowatch" in sys.argv:
            return 0
        surveiller(h, tableaux, corps, pools, ancres, PROFOND)
    finally:
        k32.CloseHandle(h)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Erreur as e:
        say(f"\n{e}")
        raise SystemExit(1)
    except KeyboardInterrupt:
        say("\nstop")