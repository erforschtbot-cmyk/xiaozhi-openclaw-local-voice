"""Welche Lampen gehoeren zu einem Raum? Zwei belegte Quellen, keine Vermutung.

1. Bewegungs-Skripte: ein aktiviertes Skript, das auf einen Bewegungsmelder
   hoert und dabei MEHRERE Lichter schaltet, beschreibt genau die Lampen seines
   Raums. Der Raum ergibt sich aus den alias-Pfaden in denselben Schreibzielen
   (alias.0.<Raum>.*). Belegt am Skript "Wohnzimmer Regenbogen": es schaltet
   WLED, Yeelight-Decke, den 5-m-Zigbee-Streifen und beide Deckenstrahler.
   Das ist die einzige Quelle, die auch Lampen ohne alias erfasst.

2. alias-Pfade: `alias.0.<Raum>.<Geraet>.<Mitglied>`. Als Lampe gilt ein
   Mitglied namens ON/DIMMER/RGB, dessen Ziel schreibbar ist. Geraete wie
   Steckdose, Luefter, Klima, Rollo werden ueber ihren Namen ausgeschlossen.
"""
from __future__ import annotations

import json
import os
import re
import unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "rooms.json")
OVERRIDES = os.path.join(HERE, "rooms_overrides.json")
SCRIPTS = "/tmp/scripts_parsed.json"

ON = re.compile(r"on\(\s*\{[^}]*?id:\s*['\"]([^'\"]+)['\"]")
SET = re.compile(r"setState(?:Delayed)?\(\s*['\"]([^'\"]+)['\"]")

LIGHT_MEMBERS = ("ON", "DIMMER", "RGB")

# Raeume, die es als Licht-Ziel NICHT gibt, auch wenn sie im alias-Pfad stehen.
# "Garten" ist nur ein Sammelordner: die Wegbeleuchtung liegt dort, ist aber ein
# eigenes Geraet. Bestaetigt vom Eigentuemer 2026-10-27: "garten hat keine
# beleuchtung oder so. das kannste raus nehmen."
EXCLUDED_ROOMS = {"garten"}
# Diese Geraete sind keine Lampen, auch wenn sie schaltbar sind.
NOT_LIGHT = (
    "steckdose", "luefter", "lufter", "klima", "heizung", "rollo", "jalousie",
    "ventilator", "computer", "drucker", "kamera", "klingel", "markise",
    "waschmaschine", "wasserkocher", "kaffeewasser", "duschwasser", "airwick",
    "rasensprenger", "gartenwasser", "regenwasser", "warmwasser", "ladezeit",
    "spuelung", "spülung", "brause", "hausalarm", "portemonnaie", "tuer",
    "haustür", "torklingel", "bewegungsmelder", "temp", "sensor", "limiter",
)


def _norm(text) -> str:
    s = unicodedata.normalize("NFC", str(text or "")).lower()
    s = s.replace("ß", "ss").replace("ä", "ae").replace("ö", "oe").replace("ü", "ue")
    return " ".join(re.sub(r"[^\w\s]", " ", s).split())


def _is_not_light(*names) -> bool:
    blob = " ".join(_norm(n) for n in names if n)
    return any(w in blob for w in NOT_LIGHT)


def _objs():
    import localmap
    return localmap._fetch_objects()


def _get_obj(oid: str):
    """Ein einzelnes Objekt nachladen. getObjects liefert Geraeteobjekte
    (type=device) auf diesem ioBroker nicht mit."""
    import json as _json
    import urllib.parse
    import urllib.request
    import localmap
    url = f"{localmap.BASE}/get/{urllib.parse.quote(oid, safe='')}/"
    try:
        with urllib.request.urlopen(url, timeout=8) as response:
            return _json.loads(response.read().decode())
    except Exception:
        return None


def find_group(objs, room: str):
    """Zigbee-Gruppe, die genau diesen Raum abdeckt — oder None.

    Gruppen sind einzeln adressierten Lampen vorzuziehen: der zigbee-Adapter
    meldet Einzelgeraete mitunter als offline (available=false) und schaltet
    sie dann nicht, waehrend dieselbe Lampe per Gruppe erreichbar ist.
    Belegt 2026-10-27: 842e14fffe19daf3 avail=false, group_1.state schaltet
    trotzdem. Bestaetigt vom Eigentuemer.
    """
    want = _norm(room)
    # Die Geraeteobjekte (zigbee.0.group_N) liefert getObjects NICHT mit.
    # Deshalb pro Gruppe einzeln nachladen — es sind nur wenige.
    for oid, obj in sorted(objs.items()):
        if not oid.startswith("zigbee.0.group_") or not oid.endswith(".state"):
            continue
        if not (obj.get("common", {}) or {}).get("write"):
            continue
        dev_id = oid.rsplit(".", 1)[0]
        dev = objs.get(dev_id)
        if dev is None:
            dev = _get_obj(dev_id)
        dev_name = str(((dev or {}).get("common", {}) or {}).get("name") or "")
        if dev_name and _norm(dev_name) == want:
            return oid
    return None


def build():
    objs = _objs()
    rooms: dict[str, dict] = {}

    def add(room: str, dp: str, via: str, label: str):
        entry = rooms.setdefault(_norm(room), {"room": room, "lights": {}, "via": {}})
        entry["lights"][dp] = label
        entry["via"][dp] = via

    # --- Quelle 1: Bewegungs-Skripte
    try:
        with open(SCRIPTS, encoding="utf-8") as handle:
            scripts = json.load(handle)
    except (OSError, ValueError):
        scripts = []

    for script in scripts:
        if not script.get("enabled") or not script.get("src"):
            continue
        writes = sorted({m.group(1) for m in SET.finditer(script["src"])})
        if len(writes) < 2:
            continue
        # Raum aus den alias-Pfaden der Ziele
        room = None
        for dp in writes:
            if dp.startswith("alias.0."):
                room = dp.split(".")[2]
                break
        if not room:
            continue
        lights = []
        for dp in writes:
            if dp.startswith("alias.0."):
                obj = objs.get(dp)
                target = (((obj or {}).get("common", {}) or {}).get("alias") or {}).get("id")
                # Nur der Ein/Aus-Punkt, nicht Dimmen/Farbe
                if target and target.endswith((".state", ".on", ".power")):
                    lights.append(target)
            elif dp.endswith((".on", ".power")):
                # sonoff-POWER sind Steckdosen (Rasensprenger), keine Lampen
                if dp.startswith(("wled.", "yeelight-2.0.")):
                    lights.append(dp)
        if len(lights) < 2:
            continue
        for dp in lights:
            add(room, dp, f"skript:{script['name']}", script["name"])

    # --- Quelle 2: alias-Pfade
    for oid, obj in objs.items():
        if not oid.startswith("alias.0."):
            continue
        parts = oid.split(".")
        if len(parts) != 5:
            continue
        room, device, member = parts[2], parts[3], parts[4]
        if member not in LIGHT_MEMBERS:
            continue
        common = obj.get("common", {}) or {}
        target = (common.get("alias") or {}).get("id")
        if not target or not common.get("write"):
            continue
        if _is_not_light(device, room):
            continue
        if _norm(room) in EXCLUDED_ROOMS:
            continue
        # Ziel muss es geben und schreibbar sein
        t = objs.get(target)
        if not t or not ((t.get("common", {}) or {}).get("write")):
            continue
        # Pro Geraet nur der Ein/Aus-Punkt
        if not target.endswith((".state", ".on", ".power")):
            continue
        add(room, target, "alias", device)

    # --- Quelle 3: vom Eigentuemer bestaetigte Zuordnungen (hoechste Prioritaet)
    try:
        with open(OVERRIDES, encoding="utf-8") as handle:
            overrides = (json.load(handle) or {}).get("rooms") or {}
    except (OSError, ValueError):
        overrides = {}
    for key, spec in overrides.items():
        key = _norm(key)
        lights = [dp for dp in (spec.get("lights") or []) if dp in objs]
        if not lights:
            continue
        entry = rooms.setdefault(key, {"room": spec.get("room") or spec.get("name"),
                                       "lights": {}, "via": {}})
        entry["room"] = spec.get("room") or entry.get("room")
        entry["lights"] = {dp: "bestaetigt" for dp in lights}
        entry["via"] = {dp: "bestaetigt" for dp in lights}

    out = []
    for key, entry in rooms.items():
        if not entry["lights"]:
            continue
        lights = sorted(entry["lights"])
        group = find_group(objs, entry["room"])
        out.append({
            "room": entry["room"],
            "key": key,
            "lights": lights,
            "via": entry["via"],
            "labels": entry["lights"],
            "group": group,
            "preferred": [group] if group else lights,
        })
    out.sort(key=lambda r: r["key"])
    payload = {"rooms": out}
    try:
        with open(CACHE, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=1)
    except OSError:
        # Siehe catalog.py: im Jarvis-Dienst ist $HOME read-only.
        pass
    return payload


def load(refresh=False):
    if not refresh and os.path.exists(CACHE):
        try:
            with open(CACHE, encoding="utf-8") as handle:
                return json.load(handle)
        except (OSError, ValueError):
            pass
    return build()


def lights_for(room_text: str):
    key = _norm(room_text)
    for entry in load()["rooms"]:
        if entry["key"] == key:
            return entry
    return None


if __name__ == "__main__":
    data = build()
    print(f"rooms.json: {len(data['rooms'])} Raeume\n")
    for r in data["rooms"]:
        print(f"{r['room']}  ({len(r['lights'])} Lampen)")
        for dp in r["lights"]:
            print(f"    {dp:52} {r['via'][dp]}")
