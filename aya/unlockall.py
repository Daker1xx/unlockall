#!/usr/bin/env python3
"""
unlockall.py - unlockall client-side VALORANT (compte neuf inclus).

1. remplit les entitlements en RAM (Skins / SkinLevels / SkinChromas)
2. ecrit la skin dans la structure de loadout locale -> rendu immediat
3. chien de garde : relocalise + reapplique

Le serveur n'est pas touche. Les autres te voient en standard.

  python unlockall.py                 menu
  python unlockall.py --all           equipement aleatoire complet + patch
  python unlockall.py --weapon vandal  choisir une skin sur une arme
  python unlockall.py --state         etat du patch
  python unlockall.py --undo          restaure tout
"""
from __future__ import annotations

import base64, ctypes, json, random, subprocess, sys, time, uuid
from ctypes import wintypes
from datetime import datetime
from pathlib import Path

import requests, urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

ROOT = Path(__file__).resolve().parent
CACHE = ROOT / "cache"
CATALOGUE_FILE = CACHE / "catalog.json"
DUMP_DIR = CACHE
import os
LOCALAPPDATA = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
LOCKFILE = LOCALAPPDATA / "Riot Games" / "Riot Client" / "Config" / "lockfile"
RIOT = [Path(r"C:\Riot Games\Riot Client\RiotClientServices.exe"),
        Path(r"C:\Program Files\Riot Games\Riot Client\RiotClientServices.exe")]

API = "https://valorant-api.com/v1"
PROC = ["VALORANT-Win64-Shipping.exe", "VALORANT.exe"]

# ItemTypeID internes (noms Riot : EquippableSkin / SkinLevel / SkinChroma ...)
TYPES = {
    "skins":       "bcef87d6-209b-46c6-8b19-fbe40bd95abc",
    "levels":      "e7c63390-eda7-46e0-bb7a-a6abdacd2433",
    "chromas":     "3ad1b2b2-acdb-4524-852f-954a76ddae0a",
    "buddies":     "77258665-71d1-4623-bc72-44db9bd5b3b3",
    "buddy_levels":"dd3bf334-87f3-40bd-b043-682a57a8dc3a",
    "cards":       "3f296c07-64c3-494c-923b-fe692a4fa1bd",
    "titles":      "de7caa6b-adf7-4588-bbd1-143831e786c6",
    "sprays":      "d5f120f8-ff8c-4aac-92ea-f2b5acbe9475",
}
DEFAUT = ["skins", "levels", "chromas"]
NIL = "00000000-0000-0000-0000-000000000000"

VM_R, VM_W, VM_O, QI = 0x10, 0x20, 0x8, 0x400
MEM_COMMIT, MEM_PRIVATE, MEM_RESERVE = 0x1000, 0x20000, 0x2000
PAGE_READWRITE = 0x04
PROTS = {0x02, 0x04, 0x08, 0x40, 0x80}
CHUNK = 4 * 1024 * 1024
PLAFOND = 0x7FFFFFFFFFFF
MAX_ITEMS = 6000
PAS_ENT = 32          # FAresEntitlement = {ItemID, InstanceID}
PAS_ELEM = 48         # element de TMap : {HashNextId, HashIndex, Key, TArray}
OFF_NUM, OFF_MAX = 8, 12
OFF_FLAGS, OFF_HASHPTR, OFF_HASHSIZE = 0x10, 0x30, 0x40

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


def ouvrir():
    if not ctypes.windll.shell32.IsUserAnAdmin():
        raise Erreur("relance en administrateur")
    pid = pid_jeu()
    if not pid:
        raise Erreur("jeu introuvable")
    h = k32.OpenProcess(VM_R | VM_W | VM_O | QI, False, pid)
    if not h:
        raise Erreur(f"OpenProcess refuse ({ctypes.get_last_error()})")
    return h


def regions(h):
    out, mbi = [], MBI()
    a = 0x10000
    while a < PLAFOND:
        if not k32.VirtualQueryEx(h, ctypes.c_void_p(a), ctypes.byref(mbi), ctypes.sizeof(mbi)):
            break
        b, t = mbi.BaseAddress, mbi.RegionSize
        if mbi.State == MEM_COMMIT and mbi.Type == MEM_PRIVATE and mbi.Protect in PROTS and t:
            out.append((b, t))
        if not t:
            break
        a = b + t
    return out


def lire(h, a, n):
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


def le(u):
    return uuid.UUID(u).bytes_le


def est_guid(b):
    return b is not None and len(b) == 16 and b != b"\x00" * 16


# hash UE : FCrc::MemCrc32 avec CRC initial 0 (pas zlib)
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


def types_possedes(ctx):
    try:
        r = requests.get(f"{ctx['pd']}/store/v1/entitlements/{ctx['puuid']}",
                         headers=ctx["entetes"], verify=False, timeout=20)
        blocs = (r.json() or {}).get("EntitlementsByTypes") or []
    except (requests.RequestException, ValueError):
        return set()
    return {b.get("ItemTypeID") for b in blocs}


# --------------------------------------------------------------- catalogue ---
def catalogue():
    if CATALOGUE_FILE.exists():
        return json.loads(CATALOGUE_FILE.read_text(encoding="utf-8"))

    def get(route):
        r = requests.get(f"{API}/{route}", timeout=40)
        r.raise_for_status()
        return r.json().get("data") or []

    pools = {k: [] for k in TYPES}
    armes = {}
    for a in get("weapons"):
        skins = []
        for s in a.get("skins", []):
            if not s.get("levels") or s["uuid"] == NIL:
                continue
            niveaux = []
            for n in s["levels"]:
                chromas = [c["uuid"] for c in n.get("chromas", []) if c["uuid"] != NIL]
                niveaux.append({"uuid": n["uuid"], "chromas": chromas})
                pools["levels"].append(n["uuid"])
                pools["chromas"] += chromas
            pools["skins"].append(s["uuid"])
            skins.append({"uuid": s["uuid"], "nom": s["displayName"], "niveaux": niveaux})
        if skins:
            armes[a["displayName"]] = sorted(skins, key=lambda x: x["nom"].lower())

    for route, cle, sous in (("buddies", "buddy_levels", "levels"),
                             ("sprays", "sprays", "levels"),
                             ("playercards", "cards", None),
                             ("playertitles", "titles", None)):
        try:
            for it in get(route):
                if it.get("uuid") and it["uuid"] != NIL:
                    pools[cle].append(it["uuid"])
                if sous:
                    for s in it.get(sous) or []:
                        if s.get("uuid") and s["uuid"] != NIL:
                            pools[cle].append(s["uuid"])
        except requests.RequestException:
            pass

    for k, v in pools.items():
        random.shuffle(v)
        pools[k] = v[:MAX_ITEMS]
    CACHE.mkdir(parents=True, exist_ok=True)
    CATALOGUE_FILE.write_text(json.dumps({"armes": armes, "pools": pools}), encoding="utf-8")
    return {"armes": armes, "pools": pools}


# ------------------------------------------------- localisation entitlements --
def _structure(h, adresse, pool_le):
    """Note un candidat TArray<FAresEntitlement> a l'adresse du header."""
    ent = lire(h, adresse, 16)
    if not ent:
        return None
    ptr = int.from_bytes(ent[0:8], "little")
    num = int.from_bytes(ent[8:12], "little")
    cap = int.from_bytes(ent[12:16], "little")
    if not ptr or num < 1 or cap < num or cap > 100000 or ptr % 8:
        return None
    ech = min(num, 4)
    contenu = lire(h, ptr, ech * PAS_ENT)
    if not contenu:
        return None
    score = 0
    for i in range(ech):
        a, b = contenu[i * 32:i * 32 + 16], contenu[i * 32 + 16:i * 32 + 32]
        if not est_guid(a) or not est_guid(b) or a == b:
            return None
        score += 2 if a in pool_le else 0
    return {"entete": adresse, "ptr": ptr, "num": num, "cap": cap,
            "score": score + min(num, 400) * 0.01}


def localiser(h, nom, pool):
    pool_le = {le(u) for u in pool}
    motif = le(TYPES[nom])
    meilleur = None
    for base, taille in regions(h):
        pos = 0
        while pos < taille:
            bloc = min(CHUNK, taille - pos)
            data = lire(h, base + pos, bloc)
            if data is None:
                break
            i = data.find(motif)
            while i != -1:
                for off in (0x10, 0x18, 0x08, 0x20, 0x28, 0x30):
                    c = _structure(h, base + pos + i + off, pool_le)
                    if c and (meilleur is None or c["score"] > meilleur["score"]):
                        c["type"], c["region"] = nom, (base, taille)
                        meilleur = c
                i = data.find(motif, i + 1)
            pos += max(1, bloc - 15)
        if meilleur and meilleur["score"] > 2.5:   # trouvé proprement, on arrete
            break
    return meilleur


def localiser_region(h, nom, pool, region):
    base, taille = region
    pool_le = {le(u) for u in pool}
    motif = le(TYPES[nom])
    pos = 0
    while pos < taille:
        bloc = min(CHUNK, taille - pos)
        data = lire(h, base + pos, bloc)
        if data is None:
            return None
        i = data.find(motif)
        while i != -1:
            for off in (0x10, 0x18, 0x08, 0x20):
                c = _structure(h, base + pos + i + off, pool_le)
                if c:
                    c["type"], c["region"] = nom, region
                    return c
            i = data.find(motif, i + 1)
        pos += max(1, bloc - 15)
    return None


# --------------------------------------------------------- insertion de cle --
def carte_depuis(h, entete):
    """Retrouve l'objet TMap a partir du header d'une valeur."""
    for k in range(1, 600):
        obj = entete - 24 - k * PAS_ELEM
        ent = lire(h, obj, 0x44)
        if not ent:
            continue
        data = int.from_bytes(ent[0:8], "little")
        num = int.from_bytes(ent[OFF_NUM:OFF_NUM + 4], "little")
        mx = int.from_bytes(ent[OFF_MAX:OFF_MAX + 4], "little")
        if data and data % 16 == 0 and num < mx < 8192 and data <= entete \
                and (entete - 24 - data) % PAS_ELEM == 0:
            return obj, data, num, mx, ent
    return None


def inserer(h, nom, pool):
    """Insere une cle ItemTypeID absente. Best effort."""
    for autre in DEFAUT:
        c = localiser(h, autre, pool)
        if not c:
            continue
        carte = carte_depuis(h, c["entete"])
        if not carte:
            continue
        obj, data, num, mx, ent = carte
        if num >= mx:
            continue
        i = num
        el = data + i * PAS_ELEM
        cle = le(TYPES[nom])
        corps = (b"\x00" * PAS_ENT).join([b""])  # placeholder
        ptr = k32.VirtualAllocEx(h, None, len(pool) * PAS_ENT,
                                 MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE)
        if not ptr:
            return None
        tampon = b"".join(le(u) + uuid.uuid5(uuid.NAMESPACE_OID, u).bytes_le for u in pool)
        if not ecrire(h, ptr, tampon):
            return None
        if not ecrire(h, el + 8, cle + ptr.to_bytes(8, "little")
                      + len(pool).to_bytes(4, "little") * 2):
            return None
        # bit d'allocation
        flags = int.from_bytes(ent[OFF_FLAGS:OFF_FLAGS + 8], "little")
        if flags:
            mot = flags + (i >> 5) * 4
            v = lire(h, mot, 4)
            if v:
                ecrire(h, mot, (int.from_bytes(v, "little") | (1 << (i & 31))).to_bytes(4, "little"))
        # chaine de hash
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
        say(f"  cle {nom} inseree dans la map")
        return {"entete": el + 24, "ptr": ptr, "num": len(pool), "cap": len(pool),
                "type": nom, "region": None, "insere": True}
    return None


# ------------------------------------------------------------------ remplir --
def dump(t):
    DUMP_DIR.mkdir(parents=True, exist_ok=True)
    f = DUMP_DIR / f"undo_{t['type']}_{datetime.now():%H%M%S}.json"
    entete = lire_global["h"] and lire(lire_global["h"], t["entete"], 16)
    contenu = lire(lire_global["h"], t["ptr"], t["num"] * PAS_ENT)
    f.write_text(json.dumps({"entete": t["entete"], "entete_hex": (entete or b"").hex(),
                             "ptr": t["ptr"], "contenu_hex": (contenu or b"").hex()}), encoding="utf-8")
    return f


lire_global = {"h": None}


def remplir(h, t, pool):
    if t.get("insere"):
        return True
    libre = max(0, t["cap"] - t["num"])
    q = min(libre, len(pool))
    if q:
        tampon = b"".join(le(pool[i]) + uuid.uuid5(uuid.NAMESPACE_OID, pool[i]).bytes_le
                          for i in range(q))
        if not ecrire(h, t["ptr"] + t["num"] * PAS_ENT, tampon):
            return False
        if not ecrire(h, t["entete"] + OFF_NUM, (t["num"] + q).to_bytes(4, "little")):
            return False
        t["num"] += q
        t["ecrit"] = q
    if t["num"] >= len(pool):
        return True
    # capacite saturee : buffer externe + redirection du pointeur
    total = len(pool)
    ptr = k32.VirtualAllocEx(h, None, total * PAS_ENT, MEM_COMMIT | MEM_RESERVE, PAGE_READWRITE)
    if not ptr:
        return True
    tampon = b"".join(le(u) + uuid.uuid5(uuid.NAMESPACE_OID, u).bytes_le for u in pool)
    if ecrire(h, ptr, tampon):
        ent = ptr.to_bytes(8, "little") + total.to_bytes(4, "little") * 2
        if ecrire(h, t["entete"], ent):
            t["ptr"], t["num"], t["cap"], t["redirige"] = ptr, total, total, True
    return True


# ------------------------------------------------------------------ loadout --
def slots(body):
    out = list(body.get("Guns") or [])
    if isinstance(body.get("Melee"), dict):
        out.append(body["Melee"])
    return out


def localiser_loadout(h, body):
    """Retrouve la structure de loadout en RAM via les GUID serveur."""
    for s in slots(body):
        if not s.get("SkinID") or s["SkinID"] == NIL:
            continue
        motif = le(s["SkinID"])
        niveau = le(s["SkinLevelID"]) if s.get("SkinLevelID") else None
        chroma = le(s["ChromaID"]) if s.get("ChromaID") else None
        for base, taille in regions(h):
            pos = 0
            while pos < taille:
                bloc = min(CHUNK, taille - pos)
                data = lire(h, base + pos, bloc)
                if data is None:
                    break
                i = data.find(motif)
                while i != -1:
                    a = base + pos + i
                    voisin = lire(h, a + 16, 32)
                    if voisin and (niveau is None or voisin[0:16] == niveau) \
                            and (chroma is None or voisin[16:32] == chroma):
                        return a
                    i = data.find(motif, i + 1)
                pos += max(1, bloc - 15)
    return None


def equip(h, corps, arme_id, skin, niveau=None, chroma=None):
    niveau = niveau or skin["niveaux"][-1]["uuid"]
    chroma = chroma or (skin["niveaux"][-1]["chromas"] or [NIL])[0]
    adresse = corps["adresses"].get(arme_id)
    if not adresse:
        return False
    ok = ecrire(h, adresse, le(skin["uuid"]) + le(niveau) + le(chroma))
    corps["voulu"][arme_id] = (skin["uuid"], niveau, chroma)
    return ok


# -------------------------------------------------------------------- etat ---
def etat(h, tableaux, corps):
    for nom, t in tableaux.items():
        v = lire(h, t["ptr"], 16)
        ok = v is not None and v[:16] != b"\x00" * 16
        say(f"  {nom:8} {'patch actif' if ok else 'PERDU'}")
    for arme, (s, _, _) in corps["voulu"].items():
        a = corps["adresses"].get(arme)
        actuel = lire(h, a, 16) if a else None
        say(f"  {arme[:8]} {'skin en place' if actuel == le(s) else 'PERDU'}")


# ------------------------------------------------------------------- boucle --
def surveiller(h, tableaux, corps, pools):
    say("\n  patch actif - Ctrl+C pour arreter")
    while True:
        time.sleep(1.5)
        if not pid_jeu():
            say("  jeu ferme")
            return
        for nom, t in list(tableaux.items()):
            v = lire(h, t["ptr"], 16)
            if v is not None and v[:16] != b"\x00" * 16:
                continue
            c = (localiser_region(h, nom, pools[nom], t["region"]) if t.get("region") else None) \
                or localiser(h, nom, pools[nom])
            if not c:
                continue
            tableaux[nom] = c
            dump(c)
            remplir(h, c, pools[nom])
            say(f"  {nom} reapplique")
        for arme, (s, n, c) in corps["voulu"].items():
            a = corps["adresses"].get(arme)
            if a and lire(h, a, 16) == le(s):
                continue
            neuf = localiser_loadout(h, corps["serveur"])
            if not neuf:
                continue
            corps["adresses"] = indexer(corps["serveur"], neuf)
            a = corps["adresses"].get(arme)
            if a:
                ecrire(h, a, le(s) + le(n) + le(c))


def indexer(body, base):
    """Retrouve l'adresse de chaque slot a partir de celle du premier."""
    ref = slots(body)[0]
    if not ref.get("SkinID"):
        return {}
    premier = localiser_loadout(lire_global["h"], body)
    if not premier:
        return {}
    out = {}
    for k, s in enumerate(slots(body)):
        if s.get("SkinID"):
            out[s["ID"]] = premier + k * 0x40
    return out


# ------------------------------------------------------------------- main ----
def lancer_jeu():
    for exe in RIOT:
        if exe.exists():
            subprocess.Popen([str(exe), "--launch-product=valorant", "--launch-patchline=live"])
            return True
    return False


def choisir(cat, arme, filtre=None):
    skins = cat["armes"].get(arme) or []
    if filtre:
        skins = [s for s in skins if filtre.lower() in s["nom"].lower()]
    for i, s in enumerate(skins[:60], 1):
        say(f"  {i:3}. {s['nom']}")
    try:
        c = int(input("Skin > ")) - 1
    except (ValueError, EOFError):
        return None
    return skins[c] if 0 <= c < min(len(skins), 60) else None


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if not ctypes.windll.shell32.IsUserAnAdmin():
        raise Erreur("relance le terminal en administrateur")

    if "--undo" in sys.argv:
        h = ouvrir()
        try:
            for f in sorted(DUMP_DIR.glob("undo_*.json")):
                d = json.loads(f.read_text(encoding="utf-8"))
                if d["contenu_hex"]:
                    ecrire(h, d["ptr"], bytes.fromhex(d["contenu_hex"]))
                if d["entete_hex"]:
                    ecrire(h, d["entete"], bytes.fromhex(d["entete_hex"]))
                f.unlink()
            say("  restaure")
        finally:
            k32.CloseHandle(h)
        return 0

    cat = catalogue()
    say("connexion au client…")
    if not pid_jeu():
        lancer_jeu()
        fin = time.monotonic() + 300
        while not pid_jeu() and time.monotonic() < fin:
            time.sleep(2)
    ctx = contexte()
    presents = types_possedes(ctx)

    demandes = sys.argv[sys.argv.index("--types") + 1].split(",") if "--types" in sys.argv else DEFAUT
    if "--all" in sys.argv:
        demandes = list(TYPES)
    pools = {k: cat["pools"][k] for k in demandes if cat["pools"].get(k)}
    say(f"  types : {', '.join(pools)}")

    h = ouvrir()
    lire_global["h"] = h
    try:
        tableaux = {}
        for nom, pool in pools.items():
            t = localiser(h, nom, pool)
            if not t:
                if TYPES[nom] not in presents:
                    t = inserer(h, nom, pool)
                if not t:
                    say(f"  {nom} : introuvable")
                    continue
            dump(t)
            remplir(h, t, pool)
            tableaux[nom] = t
            say(f"  {nom} : {t.get('ecrit', t['num'])} entree(s)"
                + (" + redirection" if t.get("redirige") else ""))
        if not tableaux:
            raise Erreur("rien localise")

        corps = {"serveur": ctx["loadout"], "adresses": {}, "voulu": {}}
        base = localiser_loadout(h, corps["serveur"])
        if not base:
            raise Erreur("structure de loadout introuvable")
        corps["adresses"] = indexer(corps["serveur"], base)
        say(f"  loadout : {len(corps['adresses'])} arme(s) verrouillee(s)")

        if "--state" in sys.argv:
            etat(h, tableaux, corps)
            return 0

        def tout_aleatoire():
            for arme in corps["adresses"]:
                skins = cat["armes"].get(arme)
                if skins:
                    equip(h, corps, arme, random.choice(skins))

        if "--all" in sys.argv:
            tout_aleatoire()
        elif "--weapon" in sys.argv:
            nom = sys.argv[sys.argv.index("--weapon") + 1].lower()
            arme = next((a for a in cat["armes"] if nom in a.lower()), None)
            if not arme:
                raise Erreur("arme inconnue")
            s = choisir(cat, arme, sys.argv[sys.argv.index("--skin") + 1]
                        if "--skin" in sys.argv else None)
            if s:
                equip(h, corps, arme, s)
        else:
            while True:
                say("\n  1. tout au hasard\n  2. choisir une arme\n  3. etat\n  0. quitter")
                c = input("> ").strip()
                if c == "1":
                    tout_aleatoire()
                elif c == "2":
                    armes = sorted(corps["adresses"].keys())
                    for i, a in enumerate(armes, 1):
                        say(f"  {i:2}. {a}")
                    try:
                        a = armes[int(input("Arme > ")) - 1]
                    except (ValueError, IndexError, EOFError):
                        continue
                    s = choisir(cat, a)
                    if s:
                        equip(h, corps, a, s)
                elif c == "3":
                    etat(h, tableaux, corps)
                elif c == "0":
                    return 0
                if c in ("1", "2"):
                    say("  applique")

        surveiller(h, tableaux, corps, pools)
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