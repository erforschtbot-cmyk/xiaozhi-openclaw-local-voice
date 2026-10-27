#!/usr/bin/env python3
"""Zweite Erkennungsstufe: HassIL (Home Assistant Intent Language).

Warum das hier liegt: Der eigene Resolver (resolver.py) deckt an/aus und
Prozent ab, aber keine Farben, keine Farbtemperatur, kein "maximum". Diese
Faelle landeten bisher beim Agenten und brauchten 7-12 s. HassIL bringt
fertige deutsche Satzvorlagen mit - die Arbeit, das alles selbst zu schreiben,
ist damit erledigt.

Rolle im System: ERGAENZUNG, kein Ersatz. control.handle() fragt zuerst den
eigenen Resolver; erst wenn der nichts findet, kommt HassIL. Damit bleiben
die bewaehrten Wege (Raum-Gruppen, preferred-Datenpunkte, lokale Ziele)
unangetastet.

Herkunft der Vorlagen (beides muss erhalten bleiben, siehe NOTICE.md):
  https://github.com/OHF-Voice/hassil     Apache-2.0
  https://github.com/OHF-Voice/intents    CC-BY 4.0
"""
from __future__ import annotations

import glob
import os
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "hassil-data")

# Gesprochene Farbe -> RGB. HassIL liefert englische Namen, die Datenpunkte
# erwarten einen JSON-String wie {"r":255,"g":0,"b":0}.
COLORS = {
    "white": (255, 255, 255),
    "black": (0, 0, 0),
    "red": (255, 0, 0),
    "orange": (255, 128, 0),
    "yellow": (255, 255, 0),
    "green": (0, 255, 0),
    "blue": (0, 0, 255),
    "purple": (128, 0, 128),
    "brown": (128, 64, 0),
}

# Von HassIL erwartete Listen, die in den Vorlagen vorkommen, aber fuer unsere
# Zwecke keine Rolle spielen (Luefterstufen, Einkaufslisten, Medien). Fehlt
# auch nur eine, bricht die Erkennung komplett ab - deshalb Platzhalter.
PLACEHOLDER = (
    "fan_speed", "todo_list_item", "shopping_list_item", "timer_name",
    "item", "message", "date", "time", "media_class", "volume_level",
    "volume_step", "position", "percentage", "device_class", "domain",
    "state", "floor", "lock_states", "cover_classes", "cover_states",
)

_lock = threading.Lock()
_intents = None


def _lad(pfad):
    import yaml
    with open(pfad, encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def _baue():
    """Baut das Intents-Objekt einmalig auf (danach im Speicher)."""
    from hassil import Intents
    from hassil.intents import (RangeSlotList, RangeType, TextSlotList,
                                WildcardSlotList)

    if not os.path.isdir(DATA):
        raise RuntimeError(f"Vorlagen fehlen: {DATA}")

    common = _lad(os.path.join(DATA, "sentences", "_common.yaml"))
    regeln = {}
    for f in sorted(glob.glob(os.path.join(DATA, "rules", "*.yaml"))):
        d = _lad(f)
        if isinstance(d, dict):
            regeln.update(d.get("expansion_rules") or {})

    gruppen = {}
    for d in sorted(glob.glob(os.path.join(DATA, "sentences", "*", ""))):
        name = os.path.basename(d.rstrip(os.sep))
        if name.startswith("_"):
            continue
        daten = []
        for f in sorted(glob.glob(os.path.join(d, "*.yaml"))):
            daten.extend(_lad(f).get("data") or [])
        if daten:
            gruppen[name] = {"data": daten}

    if not gruppen:
        raise RuntimeError("keine Intent-Gruppen gefunden")

    intents = Intents.from_dict({
        "language": "de",
        "settings": common.get("settings") or {},
        "responses": common.get("responses") or {},
        "skip_words": common.get("skip_words") or [],
        "expansion_rules": regeln,
        "intents": gruppen,
    })

    # Textlisten aus dem Vorlagen-Repo ("in" ist das gesprochene Muster).
    for f in glob.glob(os.path.join(DATA, "lists", "*.yaml")):
        for k, v in (_lad(f).get("lists") or {}).items():
            paare = [(str(x.get("in")), str(x.get("out", x.get("in"))))
                     for x in (v.get("values") or [])
                     if isinstance(x, dict) and x.get("in")]
            if paare:
                try:
                    intents.slot_lists[k] = TextSlotList.from_tuples(paare, name=k)
                except Exception:
                    pass

    for n in PLACEHOLDER:
        intents.slot_lists.setdefault(n, WildcardSlotList(n))

    # Zahlenbereiche: Prozent 0-100, Farbtemperatur in Kelvin.
    intents.slot_lists["brightness"] = RangeSlotList(
        name="brightness", start=0, stop=100, step=1, type=RangeType.PERCENTAGE)
    intents.slot_lists["color_temperature"] = RangeSlotList(
        name="color_temperature", start=1000, stop=10000, step=100,
        type=RangeType.TEMPERATURE)
    intents.slot_lists["temperature"] = RangeSlotList(
        name="temperature", start=5, stop=35, step=1, type=RangeType.TEMPERATURE)
    return intents


def _hole():
    global _intents
    if _intents is not None:
        return _intents
    with _lock:
        if _intents is None:
            _intents = _baue()
    return _intents


def _geraete():
    """Namen aus BEIDEN Quellen: lokaler Abgleich und Alexa-Katalog.

    Nur die localmap zu nehmen reicht nicht: sie fuehrt 43 Geraete mit
    belegtem lokalem Ziel, der Katalog 118. "Monitorlicht" etwa steht nur im
    Katalog - fehlt der Name, deutet HassIL ihn als Etage ("floor") um und
    der Befehl geht ins Leere (belegt 27.10.2026).
    """
    import catalog
    import localmap
    namen, caps = [], {}
    for dev in localmap.load().get("devices") or []:
        if not dev.get("caps"):
            continue
        name = dev.get("name") or ""
        if name and name not in caps:
            namen.append(name)
        caps[name] = dev["caps"]
    for dev in catalog.load().get("devices") or []:
        name = dev.get("name") or ""
        if not name or name in caps:
            continue
        writable = [c for c in (dev.get("writable") or []) if c]
        if not writable:
            continue
        namen.append(name)
        # Katalog-Faehigkeiten auf die Begriffe der localmap abbilden, damit
        # _datenpunkt_fuer() und _geraet_fuer_raum() einheitlich arbeiten.
        abbildung = {}
        for c in writable:
            if c in ("powerState", "active"):
                abbildung["onoff"] = f"{dev.get('id')}.{c}"
            elif c in ("brightness", "percentage"):
                abbildung.setdefault("brightness", f"{dev.get('id')}.{c}")
            elif c == "colorName":
                abbildung.setdefault("color", f"{dev.get('id')}.{c}")
            elif c == "colorTemperatureName":
                abbildung.setdefault("colortemp", f"{dev.get('id')}.{c}")
            elif c == "targetSetpoint":
                abbildung.setdefault("setpoint", f"{dev.get('id')}.{c}")
        if abbildung:
            caps[name] = abbildung
    return namen, caps


def _raeume():
    import json
    pfad = os.path.join(HERE, "rooms.json")
    try:
        with open(pfad, encoding="utf-8") as handle:
            daten = json.load(handle) or {}
    except (OSError, ValueError):
        return []
    return [r for r in (daten.get("rooms") or []) if r.get("room")]


def parse(text: str):
    """Satz auswerten. Gibt ein dict zurueck oder None.

    Rueckgabe: {"intent": ..., "name"/"area": ..., "brightness"/"color"/
                "temperature": ...}
    Es wird nichts geschaltet - nur erkannt.
    """
    from hassil import recognize_best
    from hassil.intents import TextSlotList

    text = " ".join(str(text or "").split())
    if not text:
        return None

    intents = _hole()
    namen, _caps = _geraete()
    raeume = _raeume()

    # Slot-Listen bei jedem Aufruf neu setzen: der Eigentuemer kann Namen und
    # Raeume im Panel aendern, ohne dass der Dienst neu startet.
    intents.slot_lists["name"] = TextSlotList.from_strings(namen, name="name")
    intents.slot_lists["area"] = TextSlotList.from_strings(
        [r["room"] for r in raeume], name="area")

    try:
        treffer = recognize_best(text, intents)
    except Exception:
        return None
    if treffer is None:
        return None

    ergebnis = {"intent": treffer.intent.name}
    for key, wert in (treffer.entities or {}).items():
        ergebnis[key] = wert.value if hasattr(wert, "value") else wert
    return ergebnis


def _datenpunkt_fuer(name, cap):
    """Passenden Datenpunkt eines Geraets fuer eine Faehigkeit liefern."""
    _namen, caps = _geraete()
    return (caps.get(name) or {}).get(cap)


def _geraet_fuer_raum(raum, cap):
    """Geraet suchen, das zu einem Raum gehoert und die Faehigkeit hat.

    rooms.json nennt die Ein/Aus-Punkte des Raums (z.B. zigbee.0.group_1.state).
    Daraus wird der Praefix gebildet und das passende localmap-Geraet gesucht,
    das dieselbe Basis fuehrt - so kommt man an brightness/color/colortemp,
    ohne die Zuordnung doppelt zu pflegen.
    """
    _namen, caps = _geraete()
    for r in _raeume():
        if (r.get("room") or "").casefold() != str(raum or "").casefold():
            continue
        praefixe = []
        for dp in (r.get("lights") or []):
            for ende in (".state", ".on", ".power"):
                if dp.endswith(ende):
                    praefixe.append(dp[: -len(ende)])
        for geraet, c in caps.items():
            for dp in c.values():
                for praefix in praefixe:
                    if str(dp).startswith(praefix + ".") and cap in c:
                        return geraet
    return None


def als_schaltbefehl(ergebnis):
    """Erkanntes Ergebnis in die Sprache von control.handle() uebersetzen.

    Rueckgabe: (datapoint, value) oder None.  Es wird nichts geschrieben.
    """
    if not isinstance(ergebnis, dict):
        return None
    intent = ergebnis.get("intent") or ""
    name = ergebnis.get("name")
    raum = ergebnis.get("area")

    def ziel(cap):
        if name:
            dp = _datenpunkt_fuer(name, cap)
            if dp:
                return dp
        if raum:
            geraet = _geraet_fuer_raum(raum, cap)
            if geraet:
                return _datenpunkt_fuer(geraet, cap)
        return None

    if intent == "HassTurnOn":
        dp = ziel("onoff") or ziel("brightness")
        return (dp, True) if dp else None
    if intent == "HassTurnOff":
        dp = ziel("onoff") or ziel("brightness")
        return (dp, False) if dp else None
    if intent == "HassLightSet":
        if "brightness" in ergebnis:
            dp = ziel("brightness")
            if not dp:
                return None
            try:
                wert = float(int(round(float(ergebnis["brightness"]))))
            except (TypeError, ValueError):
                return None
            return dp, max(0.0, min(100.0, wert))
        if "color" in ergebnis:
            dp = ziel("color")
            if not dp:
                return None
            rgb = COLORS.get(str(ergebnis["color"]).lower())
            if not rgb:
                return None
            import json as _json
            return dp, _json.dumps(
                {"r": rgb[0], "g": rgb[1], "b": rgb[2]}, separators=(",", ":"))
        if "temperature" in ergebnis:
            dp = ziel("colortemp")
            if not dp:
                return None
            try:
                kelvin = float(ergebnis["temperature"])
            except (TypeError, ValueError):
                return None
            if not 1000 <= kelvin <= 10000:
                return None
            # zigbee fuehrt Mired, gesprochen wird Kelvin: 2700 K = 370 mired.
            return dp, int(round(1000000.0 / kelvin))
    return None


if __name__ == "__main__":
    import sys
    probe = sys.argv[1:] or ["Licht Arbeitszimmer blau"]
    for satz in probe:
        e = parse(satz)
        print(f"  {satz!r:48} -> {e}")
        if e:
            print(f"      Befehl: {als_schaltbefehl(e)}")
