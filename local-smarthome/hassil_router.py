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
import re
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

def _norm_raum(text):
    """Raumnamen vergleichbar machen (Gross/Klein, Umlaute)."""
    t = str(text or "").strip().casefold()
    for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss")):
        t = t.replace(a, b)
    return "".join(ch for ch in t if ch.isalnum())


# Weisswoerter und ihre Farbtemperatur in KELVIN.
#
# Werte vom Eigentuemer bestaetigt (28.10.2026):
#   weiss 4000, warmes weiss 2200, warmweiss 2700,
#   Tageslicht 5500, Kaltweiss 7000, kaltes weiss 7000
#
# Zwei Dinge sind hier wichtig und waren vorher falsch:
#
# 1) "warmes weiss" (2200) ist NICHT "warmweiss" (2700). Beides auf denselben
#    Wert abzubilden war falsch. Deshalb eigene Tabelle statt Synonyme.
#
# 2) Die colortemp-Datenpunkte erwarten KELVIN, nicht Mired. Belegt an den
#    vorbestehenden Werten: a4c13834b389ea43 stand auf 2200, group_1 auf 7000.
#    Als Mired waeren das 455 K bzw. 143 K - physikalisch unmoeglich. Meine
#    Umrechnung 1000000/kelvin war also falsch und ist entfernt.
#
# Reihenfolge: laengste Muster zuerst, damit "warmes weiss" nicht von
# "warmweiss" geschluckt wird.
_WEISS_KELVIN = (
    # Nur diese sechs Begriffe sind freigegeben (Eigentuemer, 28.10.2026).
    # Jeweils beide Schreibweisen, weil die Spracherkennung mal "ß" und mal
    # "ss" liefert - das ist derselbe Name, keine zweite Variante.
    #   weiss 4000, warmes weiss 2200, warmweiss 2700,
    #   Tageslicht 5500, Kaltweiss 7000, kaltes weiss 7000
    #
    # Reihenfolge: laengste Muster zuerst, damit "warmes weiss" nicht von
    # "warmweiss" geschluckt wird.
    ("warmes weiß", 2200),
    ("warmes weiss", 2200),
    ("warmweiß", 2700),
    ("warmweiss", 2700),
    ("tageslicht", 5500),
    ("kaltes weiß", 7000),
    ("kaltes weiss", 7000),
    ("kaltweiß", 7000),
    ("kaltweiss", 7000),
    ("weiß", 4000),
    ("weiss", 4000),
)

# Fuellwoerter, die nach dem Weisswort uebrig bleiben koennen.
_WEISS_FUELLER = ("warmes", "warme", "kaltes", "kalte",
                  "weiß", "weiss", "licht", "tages")


def weiss_kelvin(text: str):
    """Farbtemperatur aus dem Satz lesen - unabhaengig von HassIL.

    Gibt Kelvin zurueck oder None. Textbasiert, weil die Vorlagen diese
    Woerter nur teilweise kennen ("kaltlicht" fehlt ganz) und "warmes weiss"
    faelschlich als Etage auslegen.
    """
    klein = " ".join(str(text or "").lower().split())
    for muster, kelvin in _WEISS_KELVIN:
        # Wortgrenzen beachten: sonst steckt "weiß" auch in "neutralweiß"
        # und "kaltlicht" wuerde ueber "licht" getroffen. Nur eigenstaendige
        # Woerter zaehlen (belegt 28.10.2026).
        if re.search(r"(?<!\w)" + re.escape(muster) + r"(?!\w)", klein):
            return kelvin
    return None


def weiss_rest(text: str) -> str:
    """Was vom Satz uebrig bleibt, wenn man die Weisswoerter entfernt.

    Fuer "Lampe Schlafzimmer warmes weiss" bleibt "Lampe Schlafzimmer" -
    daraus laesst sich das Geraet finden, obwohl HassIL den Rest als Etage
    ausgelegt hat.
    """
    klein = " ".join(str(text or "").lower().split())
    for muster, _ in _WEISS_KELVIN:
        klein = klein.replace(muster, " ")
    for wort in _WEISS_FUELLER:
        klein = klein.replace(wort, " ")
    return " ".join(klein.split())


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

    # Weisston ZUERST bestimmen - er rettet Faelle, die HassIL gar nicht
    # kennt. Belegt 28.10.2026: "Lampe Schlafzimmer kaltlicht" ergab None,
    # weil das Wort in keiner Vorlage steht. Ohne diese Vorabpruefung lief
    # der Befehl ins Leere, obwohl der Weisswert eindeutig ist.
    kelvin = weiss_kelvin(text)

    try:
        treffer = recognize_best(text, intents)
    except Exception:
        treffer = None

    if treffer is None:
        if not kelvin:
            return None
        # Nur der Weisston ist bekannt: ein Ersatz-Ergebnis bauen. Das Geraet
        # oder den Raum findet spaeter als_schaltbefehl() ueber weiss_rest().
        ergebnis = {"intent": "HassLightSet", "kelvin": kelvin}
        rest = weiss_rest(text)
        if rest:
            ergebnis["_rest"] = rest
        return ergebnis

    ergebnis = {"intent": treffer.intent.name}
    for key, wert in (treffer.entities or {}).items():
        ergebnis[key] = wert.value if hasattr(wert, "value") else wert

    if kelvin:
        ergebnis["kelvin"] = kelvin
    return ergebnis


# Zwischenspeicher fuer die Rolle eines Datenpunkts. Die Rolle sagt, welches
# Format erwartet wird - und genau daran scheiterte "weiß": hex_color
# (level.color.rgb) will "#rrggbb", group_1.color (level.color) will
# {"r":..,"g":..,"b":..}. Falsch geschrieben nimmt der Adapter den Wert
# trotzdem an, die Lampe reagiert aber nicht (belegt 28.10.2026).
_ROLLEN = {}


def _rolle(datapoint: str):
    """Rolle eines Datenpunkts aus ioBroker (gecacht)."""
    if not datapoint:
        return None
    if datapoint in _ROLLEN:
        return _ROLLEN[datapoint]
    import json as _json
    import urllib.parse
    import urllib.request
    import settings
    rolle = None
    try:
        url = f"{settings.base()}/get/{urllib.parse.quote(datapoint, safe='')}/"
        with urllib.request.urlopen(url, timeout=5) as response:
            daten = _json.loads(response.read().decode())
        rolle = ((daten or {}).get("common") or {}).get("role")
    except Exception:
        rolle = None
    _ROLLEN[datapoint] = rolle
    return rolle


def farbe_als_wert(datapoint: str, rgb) -> str:
    """Farbe im Format schreiben, das der Ziel-Datenpunkt erwartet.

    level.color.rgb  -> "#rrggbb"   (Einzelgeraete, hex_color)
    level.color      -> {"r":..}    (Zigbee-Gruppen)
    """
    import json as _json
    r, g, b = rgb
    rolle = (_rolle(datapoint) or "").lower()
    if "rgb" in rolle:
        return f"#{r:02x}{g:02x}{b:02x}"
    return _json.dumps({"r": r, "g": g, "b": b}, separators=(",", ":"))


def weiss_als_temperatur(erkannt: dict):
    """Weisston aus einem erkannten Satz lesen - Kelvin oder None.

    Die Werte kommen aus der Tabelle _WEISS_KELVIN und wurden vom
    Eigentuemer bestaetigt (28.10.2026):
      weiss 4000, warmes weiss 2200, warmweiss 2700,
      Tageslicht 5500, Kaltweiss 7000, kaltes weiss 7000

    "warmes weiss" und "warmweiss" sind dabei VERSCHIEDEN - frueher wurden
    beide auf 2700 abgebildet, das war falsch.

    Die colortemp-Datenpunkte fuehren KELVIN, nicht Mired. Belegt an den
    vorbestehenden Werten 2200 (a4c13834b389ea43) und 7000 (group_1);
    als Mired waeren das 455 K bzw. 143 K, also unmoeglich.
    """
    if not isinstance(erkannt, dict):
        return None
    # parse() legt den erkannten Weisston bereits als "kelvin" ab - das ist
    # der verlaessliche Weg, weil dort der ganze Satz bekannt ist.
    if erkannt.get("kelvin"):
        try:
            return int(erkannt["kelvin"])
        except (TypeError, ValueError):
            return None
    # Rueckfall: aus dem floor schliessen (aeltere Aufrufer).
    farbe = str(erkannt.get("color") or "").lower()
    if farbe != "white":
        return None
    floor = str(erkannt.get("floor") or "").lower()
    if "kerze" in floor:
        return 1900
    if "warm" in floor:
        return 2200
    if "kalt" in floor:
        return 7000
    if "tages" in floor or "tag" in floor:
        return 5500
    return 4000


def _ident_fuer(name):
    """Datenpunkt-id zu einem Sprechnamen (oder None).

    Wird gebraucht, weil Vorgaben aus dem Panel unter der id liegen, der
    Sprachweg aber mit Namen arbeitet.
    """
    if not name:
        return None
    try:
        import catalog
    except Exception:
        return None
    for dev in catalog.load().get("devices") or []:
        if (dev.get("name") or "") == name:
            return dev.get("id")
    return None


def _datenpunkt_fuer(name, cap):
    """Passenden Datenpunkt eines Geraets fuer eine Faehigkeit liefern.

    Eine Vorgabe des Eigentuemers hat Vorrang - auch pro Faehigkeit. Belegt
    28.10.2026: der Wohnzimmer-Deckenstrahler fuehrt Farbe unter hex_color
    (role level.color.rgb, "#rrggbb"), waehrend group_1.color ein JSON
    {r,g,b} erwartet. Ohne diese Pruefung schrieb die Farbstufe das falsche
    Format auf den falschen Punkt.
    """
    import overrides
    _namen, caps = _geraete()
    # Die Vorgabe steht je nach Herkunft unter der Datenpunkt-id ODER unter
    # dem Sprechnamen. Beide probieren - sonst wird sie uebersehen, wenn das
    # Panel sie unter der id gespeichert hat (belegt 28.10.2026).
    vorgabe = overrides.target_for(name, _ident_fuer(name), cap=cap)
    if vorgabe and vorgabe.get("datapoint"):
        return vorgabe["datapoint"]
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


def _geraet_raum(geraet):
    """Raum eines Geraets bestimmen, ueber die Datenpunkt-Praefixe.

    rooms.json nennt je Raum die Ein/Aus-Punkte (z.B. zigbee.0.group_1.state).
    Ein Geraet gehoert zu diesem Raum, wenn eines seiner Ziele unter demselben
    Praefix liegt.
    """
    _namen, caps = _geraete()
    meine = [str(d) for d in (caps.get(geraet) or {}).values()]
    # Manche Geraete tragen den Raum im Namen ("arbeitszimmer Heizung",
    # "Badezimmer Heizung"), ihre Datenpunkte liegen aber nicht unter einem
    # Raum-Praefix (0_userdata.*). Deshalb zuerst der Name.
    g_norm = _norm_raum(geraet)
    for r in _raeume():
        if _norm_raum(r.get("room")) and _norm_raum(r.get("room")) in g_norm:
            return r.get("room")
    for r in _raeume():
        for dp in (r.get("lights") or []):
            for ende in (".state", ".on", ".power"):
                if dp.endswith(ende):
                    praefix = dp[: -len(ende)]
                    if any(d.startswith(praefix + ".") for d in meine):
                        return r.get("room")
    return None


def _geraet_mit_name_und_raum(name, raum):
    """Geraet suchen, dessen Name Name UND Raum enthaelt.

    "Heizung" + "Badezimmer" -> "Badezimmer Heizung". Genau dieses Geraet ist
    gemeint; das reine "Heizung" gehoert zum Arbeitszimmer.
    """
    _namen, caps = _geraete()
    n_raum, n_name = _norm_raum(raum), _norm_raum(name)
    treffer = None
    for geraet in caps:
        g = _norm_raum(geraet)
        if n_raum in g and n_name in g:
            # kuerzester passender Name gewinnt ("Badezimmer Heizung" vor
            # "Badezimmer Heizung links")
            if treffer is None or len(geraet) < len(treffer):
                treffer = geraet
    return treffer


def als_schaltbefehl(ergebnis):
    """Erkanntes Ergebnis in die Sprache von control.handle() uebersetzen.

    Rueckgabe: (datapoint, value) oder None.  Es wird nichts geschrieben.
    """
    if not isinstance(ergebnis, dict):
        return None
    intent = ergebnis.get("intent") or ""
    name = ergebnis.get("name")
    raum = ergebnis.get("area")

    # "floor" ist bei uns bedeutungslos: es gibt keine Etagen. Trifft HassIL
    # darauf, hat es meist den Namen nicht verstanden, sondern Reste als Etage
    # gedeutet. Belegt 27.10.2026: "monitorlich an" -> name="Monitor",
    # floor="lich"; geschaltet wurde der Monitor statt des Monitorlichts.
    #
    # AUSNAHME Weiss: "warmes weiss" / "kaltes weiss" legt HassIL ebenfalls
    # als floor aus ("warmes"), obwohl es fachlich eine Farbtemperatur ist.
    # Pauschal zu verwerfen liess "Lampe Schlafzimmer warmes weiss" ins Leere
    # laufen (belegt 28.10.2026). Diese Fuellwoerter sind daher erlaubt.
    _floor = str(ergebnis.get("floor") or "").strip().lower()
    if _floor and weiss_als_temperatur(ergebnis) is None:
        return None

    # Bei "Lampe Schlafzimmer warmes weiss" steckt der Geraete- UND Raurname
    # im floor ("Schlafzimmer warmes"), nicht in name/area. Ohne Nachschlagen
    # findet die Zielsuche nichts und der Befehl laeuft ins Leere
    # (belegt 28.10.2026). Deshalb aus dem floor ergaenzen, was fehlt.
    # Geraet oder Raum aus dem Resttext bestimmen. Zwei Quellen:
    #   _rest  - von parse() gesetzt, wenn HassIL den Satz nicht kannte
    #            ("Lampe Schlafzimmer kaltlicht": kaltlicht fehlt in den
    #            Vorlagen, der Rest ist "lampe schlafzimmer")
    #   floor  - wenn HassIL den Rest als Etage ausgelegt hat
    #            ("Lampe Schlafzimmer warmes weiss" -> floor "Schlafzimmer
    #            warmes")
    # Beides wird gleich behandelt: Fuellwoerter raus, dann Name oder Raum
    # suchen. Ohne diesen Schritt laufen genau diese Saetze ins Leere
    # (belegt 28.10.2026).
    # Welche Faehigkeit wird gebraucht? Damit die Kandidatensuche nicht das
    # kuerzeste Geraet nimmt, das zufaellig passt: "schlafzimmer" trifft
    # "Klima Schlafzimmer" (nur onoff), "Lampe Schlafzimmer" (alles) und
    # "Schlafzimmer Ambilight" (nur onoff). Ohne Filter gewann das kuerzeste -
    # "Klima Schlafzimmer" - und der Farbbefehl lief ins Leere (belegt
    # 28.10.2026).
    if ergebnis.get("kelvin") or "temperature" in ergebnis:
        _noetig = "colortemp"
    elif "brightness" in ergebnis:
        _noetig = "brightness"
    elif "color" in ergebnis:
        _noetig = "color"
    elif intent in ("HassTurnOn", "HassTurnOff"):
        _noetig = "onoff"
    else:
        _noetig = None

    _restkandidat = str(ergebnis.get("_rest") or "").strip().lower()
    if not _restkandidat and _floor:
        _restkandidat = _floor
    if _restkandidat and not name:
        _rest = _restkandidat
        for _wort in ("warmes", "warme", "warm", "kaltes", "kalte", "kalt",
                      "neutrales", "neutrale", "neutral", "tageslicht",
                      "tages", "kerzenlicht", "kerze", "licht", "weiss",
                      "weiß"):
            _rest = _rest.replace(_wort, " ")
        _rest = " ".join(_rest.split())
        if _rest:
            _namen, _caps = _geraete()
            # 1) exakter Geraetename
            _treffer = [n for n in _caps if _norm_raum(n) == _norm_raum(_rest)]
            if _treffer:
                name = _treffer[0]
            else:
                # 2) Name enthaelt den Rest (oder umgekehrt), z.B.
                #    Rest "schlafzimmer" -> Gerät "Lampe Schlafzimmer".
                #    Kuerzester Treffer gewinnt, damit nicht mehrere
                #    aehnliche Geraete in Frage kommen.
                _rest_n = _norm_raum(_rest)
                _alle = [n for n in _caps if _rest_n and _rest_n in _norm_raum(n)]
                # Erst die Geraete, die die gebrauchte Faehigkeit haben.
                if _noetig:
                    _passend = [n for n in _alle if _noetig in (_caps[n] or {})]
                    if _passend:
                        _alle = _passend
                _kandidaten = sorted(_alle, key=len)
                if _kandidaten:
                    name = _kandidaten[0]
                else:
                    # 3) Raum
                    _raumtreffer = [r["room"] for r in _raeume()
                                    if _norm_raum(r["room"]) == _rest_n]
                    if _raumtreffer:
                        raum = raum or _raumtreffer[0]

    # Name UND Raum genannt: dann muss ein Geraet zu beidem passen.
    # Belegt 27.10.2026: "heizung badezimmer an" -> name="Heizung",
    # area="Badezimmer"; ohne diese Pruefung wurde die Heizung im
    # ARBEITSZIMMER geschaltet (0_userdata.0.Arbeitszimmer.Heizung) - der
    # falsche Raum, und das faellt niemandem auf, weil es funktioniert.
    if name and raum:
        passend = _geraet_mit_name_und_raum(name, raum)
        if passend:
            name = passend
        else:
            eigener = _geraet_raum(name)
            if eigener is None or _norm_raum(eigener) != _norm_raum(raum):
                # Kein Geraet passt zu beidem, und der Raum des Namens ist
                # unbekannt oder ein anderer: nicht raten.
                return None

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
            # "weiss" ist eine Farbtemperatur, keine Farbe. Kelvin direkt -
            # die Datenpunkte fuehren Kelvin, nicht Mired (belegt 28.10.2026
            # an den vorbestehenden Werten 2200 und 7000).
            kelvin = weiss_als_temperatur(ergebnis)
            if kelvin:
                dp = ziel("colortemp")
                if dp:
                    return dp, int(kelvin)
            dp = ziel("color")
            if not dp:
                return None
            rgb = COLORS.get(str(ergebnis["color"]).lower())
            if not rgb:
                return None
            return dp, farbe_als_wert(dp, rgb)
        if "temperature" in ergebnis or ergebnis.get("kelvin"):
            dp = ziel("colortemp")
            if not dp:
                return None
            # Unser Wert hat Vorrang vor der Tabelle der Vorlagen: die kennt
            # Tageslicht 6500 und Kaltweiss 4000, der Eigentuemer hat aber
            # 5500 und 7000 bestaetigt (28.10.2026). Belegt: ohne diese
            # Reihenfolge schrieb "Tageslicht" 6500 statt 5500.
            if ergebnis.get("kelvin"):
                try:
                    return dp, int(ergebnis["kelvin"])
                except (TypeError, ValueError):
                    return None
            try:
                kelvin = float(ergebnis["temperature"])
            except (TypeError, ValueError):
                return None
            if not 1000 <= kelvin <= 10000:
                return None
            # Kelvin direkt schreiben: die colortemp-Punkte fuehren Kelvin.
            return dp, int(kelvin)
    return None


if __name__ == "__main__":
    import sys
    probe = sys.argv[1:] or ["Licht Arbeitszimmer blau"]
    for satz in probe:
        e = parse(satz)
        print(f"  {satz!r:48} -> {e}")
        if e:
            print(f"      Befehl: {als_schaltbefehl(e)}")
