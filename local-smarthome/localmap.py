"""Lokale Schaltziele aus dem iot-/alias-Adapter lesen und mit alexa2 abgleichen.

Der iot-Adapter publiziert Objekte mit `common.smartName.de` (= Name in der
Alexa-App) und `common.alias.id` (= der echte lokale Datenpunkt). Damit ist die
Zuordnung Alexa-Name -> lokaler Datenpunkt belegt, nicht geraten.

Ein Geraet hat oft mehrere Alias-Geschwister (ON, DIMMER, RGB, TEMPERATURE).
Welches Geschwister welche Aktion traegt, entscheidet die `role`.
"""
from __future__ import annotations

import json
import os
import re
import time
import unicodedata
import urllib.request
from collections import defaultdict

BASE = os.environ.get("IOBROKER_SIMPLE_API", "http://192.168.178.24:8087")
HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "localmap.json")
MAX_AGE = 6 * 3600

# Nur diese Praefixe gelten als "lokal" (kein Amazon, kein Cloud-Dienst).
LOCAL_PREFIX = (
    "zigbee.", "shelly.", "tuya.", "wled.", "mqtt.", "tasmota.", "esphome.",
    "ham.", "yeelight", "javascript.", "0_userdata.", "modbus.", "knx.",
    "zwave.", "homekit.", "sonoff.", "sainlogic.", "ecovacs.",
)
# Ausdruecklich NICHT lokal: der Amazon-Weg selbst.
CLOUD_PREFIX = ("alexa2.", "iot.", "cloud.", "smartthings.")

# "switch.power" gehoert dazu: bei Luefter und Temperatur Klimaanlage sitzt
# der echte Ein/Aus-Schalter in einem .POWER-Geschwister ohne smartName
# (belegt 2026-10-26), waehrend der smartName am .SET/.ACTUAL haengt.
ROLE_ON = ("switch", "switch.light", "switch.power", "switch.outlet", "socket")
ROLE_DIM = ("level.dimmer",)
ROLE_COLOR = ("level.color.rgb",)
ROLE_TEMP_COLOR = ("level.color.temperature",)
ROLE_SETPOINT = ("level.temperature", "value.temperature")


def normalize(text) -> str:
    s = unicodedata.normalize("NFC", str(text or "")).lower()
    s = s.replace("ß", "ss").replace("ä", "ae").replace("ö", "oe").replace("ü", "ue")
    return " ".join(re.sub(r"[^\w\s]", " ", s).split())


def _is_local(oid: str) -> bool:
    if oid.startswith(CLOUD_PREFIX):
        return False
    return oid.startswith(LOCAL_PREFIX)


def _fetch_objects():
    with urllib.request.urlopen(BASE + "/getObjects", timeout=60) as response:
        return json.loads(response.read().decode())


def _smart_name(common) -> str | None:
    smart = common.get("smartName")
    if isinstance(smart, dict):
        name = smart.get("de") or next((v for v in smart.values() if isinstance(v, str)), None)
        return name or None
    if isinstance(smart, str):
        return smart or None
    return None


def build():
    objects = _fetch_objects()

    def target_role(target: str):
        """Rolle des Ziels. Die Alias-Geschwister tragen oft keine eigene role;
        dann gilt die Rolle des echten Datenpunkts."""
        obj = objects.get(target)
        if not obj:
            return None
        return (obj.get("common", {}) or {}).get("role")

    # Alle Alias-Ziele einsammeln, gruppiert nach Geraetebasis
    # ("alias.0.Kueche.Lampe_Kueche" -> DIMMER/RGB/TEMPERATURE).
    alias_children = defaultdict(list)
    for oid, obj in objects.items():
        common = obj.get("common", {}) or {}
        target = (common.get("alias") or {}).get("id")
        if not target:
            continue
        base = oid.rsplit(".", 1)[0]
        smart = common.get("smartName")
        alias_children[base].append({
            "oid": oid,
            "target": target,
            "role": common.get("role"),
            "type": common.get("type"),
            "write": bool(common.get("write")),
            "name": _smart_name(common),
            "smartType": smart.get("smartType") if isinstance(smart, dict) else None,
        })

    # Ein Eintrag pro Alexa-Name
    devices: dict[str, dict] = {}

    def add_capability(name, entry):
        rec = devices.setdefault(name, {"name": name, "caps": {}, "source": "smartName"})
        target = entry["target"]
        # Ein Alias kann auf einen geloeschten Datenpunkt zeigen (belegt:
        # Lampe_Schlafzimmer.ON -> zigbee.0.0017880103546aa8.state existiert
        # nicht). So ein Ziel darf nie geschrieben werden.
        if target not in objects:
            rec.setdefault("broken", []).append(f"{entry['oid']} -> {target} (fehlt)")
            return
        role = entry["role"] or target_role(target) or ""
        # Rueckfallebene: Alias-Kind heisst ON/SET und ist boolean.
        if not role:
            leaf = entry["oid"].rsplit(".", 1)[-1].upper()
            if leaf in ("ON", "SET", "SWITCH", "STATE") and entry["type"] == "boolean":
                role = "switch"
        # Letzte Ebene: der smartType sagt, was Alexa daraus macht. 0_userdata-
        # Schalter tragen oft nur role=state (belegt: Klima_Schlafzimmer).
        if role in ("", "state", "switch") and entry.get("smartType"):
            st = entry["smartType"].upper()
            if st in ("SWITCH", "SMARTPLUG", "SOCKET", "LIGHT", "OUTLET") and entry["type"] == "boolean":
                role = "switch"
            elif st in ("DIMMER", "LIGHT") and entry["type"] == "number":
                role = "level.dimmer"
            elif st in ("THERMOSTAT",) and entry["type"] == "number":
                role = "level.temperature"
        if not _is_local(target):
            rec.setdefault("blocked", []).append(target)
            return
        if not entry["write"]:
            return
        if role in ROLE_ON:
            rec["caps"].setdefault("onoff", target)
        elif role in ROLE_DIM:
            rec["caps"].setdefault("brightness", target)
        elif role in ROLE_COLOR:
            rec["caps"].setdefault("color", target)
        elif role in ROLE_TEMP_COLOR:
            rec["caps"].setdefault("colortemp", target)
        elif role in ROLE_SETPOINT:
            rec["caps"].setdefault("setpoint", target)

    # 1) Alias-Geraete: smartName sitzt oft nur auf EINEM Geschwister.
    for base, children in alias_children.items():
        name = next((c["name"] for c in children if c["name"]), None)
        if not name:
            continue
        for child in children:
            add_capability(name, child)

    # 2) Direkte smartName-Objekte ohne Alias (z. B. yeelight, javascript).
    for oid, obj in objects.items():
        common = obj.get("common", {}) or {}
        if (common.get("alias") or {}).get("id"):
            continue
        name = _smart_name(common)
        if not name:
            continue
        smart = common.get("smartName")
        add_capability(name, {
            "oid": oid, "target": oid, "role": common.get("role"),
            "type": common.get("type"), "write": bool(common.get("write")),
            "name": name,
            "smartType": smart.get("smartType") if isinstance(smart, dict) else None,
        })

    # --- Ein/Aus ableiten, wenn kein ON-Alias existiert -------------------
    # Belegt bei Lampe Kueche und Lampe Schlafzimmer (2026-10-26): beide haben
    # kein funktionierendes ON-Alias, aber DIMMER/RGB/TEMPERATURE. Der passende
    # Schalter liegt auf DEMSELBEN Zigbee-Geraet, auf das die Geschwister zeigen.
    #
    # Modelle laut zigbee-Geraeteobjekt: 0017880103544b64 = Philips Hue
    # (9290012573A), a4c138ae1ecf2c06 = Tuya TS0505B_1. NICHT Osram - der Name
    # "Osram Switch" gehoert nur zu einem Skript, nicht zur Hardware.
    # Das ist keine Vermutung: die Geraete-ID stammt aus dem Alias, und der
    # <geraet>.state wird nur uebernommen, wenn er existiert, schreibbar ist
    # und eine switch-Rolle hat. Bei Mehrdeutigkeit wird nichts abgeleitet.
    for name, rec in devices.items():
        caps = rec.setdefault("caps", {})
        if "onoff" in caps:
            continue
        zigbee_devices = set()
        for target in caps.values():
            if target.startswith("zigbee.0."):
                zigbee_devices.add(".".join(target.split(".")[:3]))
        candidates = []
        for dev in sorted(zigbee_devices):
            obj = objects.get(f"{dev}.state")
            if not obj:
                continue
            common = obj.get("common", {}) or {}
            if common.get("write") and str(common.get("role") or "").startswith("switch"):
                candidates.append(f"{dev}.state")
        if len(candidates) == 1:
            caps["onoff"] = candidates[0]
            rec["derived"] = [f"onoff aus {candidates[0]} (kein ON-Alias vorhanden)"]

    # --- Bruecken: 0-100-Wert -> Schalter, von einem aktiven Skript getrieben
    # Belegt am Beispiel Terrasse (2026-10-26): das Geraet "Terrasse Regebogen"
    # ist ein Prozentwert (0-100). Das aktive Skript "Terrasse Regenbogen" macht
    # daraus: !=0 -> wled.on=true, 0 -> wled.on=false. Der Wert ist also ein
    # echter Ein/Aus-Schalter, nur mit 0-100 statt boolean.
    # Uebernommen wird nur, wenn das Skript GENAU EINEN Schalter setzt.
    try:
        with open(os.path.join(HERE, "bridges.json"), encoding="utf-8") as handle:
            bridges = json.load(handle)
    except (OSError, ValueError):
        bridges = {}
    for dp, info in (bridges or {}).items():
        switches = [t for t in info.get("switches") or [] if t in objects]
        if len(switches) != 1:
            continue
        obj = objects.get(dp)
        if not obj:
            continue
        common = obj.get("common", {}) or {}
        name = _smart_name(common)
        if not name:
            continue
        rec = devices.setdefault(name, {"name": name, "caps": {}, "source": "bridge"})
        rec.setdefault("caps", {})["onoff"] = switches[0]
        rec.setdefault("derived", []).append(
            f"onoff ueber {dp} (0-100 -> Schalter {switches[0]})"
        )

    result = []
    for name, rec in devices.items():
        caps = rec.get("caps") or {}
        broken = sorted(set(rec.get("broken") or []))
        # Nicht stillschweigend verwerfen: ein Geraet, dessen Ziele alle
        # kaputt sind, muss in der Unstimmigkeitsliste auftauchen.
        if not caps and not broken:
            continue
        result.append({
            "name": name,
            "key": normalize(name),
            "caps": caps,
            "blocked": sorted(set(rec.get("blocked") or [])),
            "broken": broken,
            "derived": sorted(set(rec.get("derived") or [])),
        })
    result.sort(key=lambda item: item["key"])
    payload = {"builtAt": int(time.time()), "base": BASE, "devices": result}
    tmp = CACHE + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=1)
        os.replace(tmp, CACHE)
    except OSError:
        # Siehe catalog.py: im Jarvis-Dienst ist $HOME read-only.
        pass
    return payload


def load(refresh=False):
    if not refresh and os.path.exists(CACHE):
        try:
            with open(CACHE, encoding="utf-8") as handle:
                payload = json.load(handle)
            if payload.get("devices"):
                # Auch ein aelterer Cache wird benutzt: im Jarvis-Dienst ist
                # $HOME read-only, ein Neuaufbau kostet dort rund 1-3 s pro
                # Befehl. Erneuert wird ausserhalb der Sandbox mit
                # "python3 <modul>.py"; das ist der einzige vorgesehene Weg.
                return payload
        except (OSError, ValueError):
            pass
    return build()


if __name__ == "__main__":
    data = build()
    print(f"localmap.json: {len(data['devices'])} lokal schaltbare Alexa-Geraete")
    for dev in data["devices"]:
        print(f"  {dev['name'][:28]:28} {dev['caps']}")
    derived = [d for d in data["devices"] if d.get("derived")]
    if derived:
        print("\nAbgeleitet (kein ON-Alias, Ziel per Geraete-ID verifiziert):")
        for dev in derived:
            for d in dev["derived"]:
                print(f"  {dev['name']}: {d}")
    broken = [d for d in data["devices"] if d.get("broken")]
    if broken:
        print("\nDefekte Alias-Ziele (werden NICHT geschrieben):")
        for dev in broken:
            for b in dev["broken"]:
                print(f"  {dev['name']}: {b}")
