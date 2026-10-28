"""Gruppen: mehrere Geräte, die ein Sprachassistent gemeinsam schaltet.

Wozu das da ist
---------------
Bisher brauchte jeder Befehl ein Gerät oder einen Raum im Satz ("Licht
Arbeitszimmer an", "Wohnzimmer an"). Auf dem Gerät, das im Wohnzimmer steht,
soll aber "Licht an" genügen.

Eine Gruppe besteht aus:
  name        - nur eine Bezeichnung, ohne Bedeutung fürs Schalten
  assistants  - Device-IDs der Sprachgeräte, die diese Gruppe bedienen
  devices     - Sprechnamen der Geräte, die geschaltet werden

Entscheidend ist also NICHT der Gruppenname, sondern welcher Sprachassistent
das Wachwort gehört hat. Deshalb wird die Zuordnung über die Device-ID aus
room-map.json gesucht, nicht über einen gesprochenen Raumnamen.

Abgrenzung
----------
"Wohnzimmer an" nennt einen Raum - das beantwortet weiterhin room_batch() aus
rooms.json. Eine Gruppe greift nur, wenn im Satz WEDER Gerät NOCH Raum steht.
"""
from __future__ import annotations

import json
import os
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
DATEI = os.path.join(HERE, "groups.json")

_LEER = {"groups": []}


def _lesen() -> dict:
    try:
        with open(DATEI, encoding="utf-8") as handle:
            daten = json.load(handle) or {}
    except (OSError, ValueError):
        return dict(_LEER)
    if not isinstance(daten, dict):
        return dict(_LEER)
    if not isinstance(daten.get("groups"), list):
        daten["groups"] = []
    return daten


def _schreiben(daten: dict) -> None:
    """Atomar schreiben: kein halber Stand bei Absturz oder Stromausfall."""
    ordner = os.path.dirname(DATEI)
    os.makedirs(ordner, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=ordner, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(daten, handle, ensure_ascii=False, indent=1)
            handle.write("\n")
        os.replace(tmp, DATEI)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _saeubern(liste) -> list:
    """Doppelte und leere Einträge entfernen, Reihenfolge halten."""
    gesehen, out = set(), []
    for eintrag in liste or []:
        text = str(eintrag or "").strip()
        if not text or text in gesehen:
            continue
        gesehen.add(text)
        out.append(text)
    return out


def liste() -> list:
    """Alle Gruppen, jede mit gesäuberten Feldern."""
    out = []
    for gruppe in _lesen()["groups"]:
        if not isinstance(gruppe, dict):
            continue
        name = " ".join(str(gruppe.get("name") or "").split())
        if not name:
            continue
        out.append({
            "name": name,
            "assistants": _saeubern(gruppe.get("assistants")),
            "devices": _saeubern(gruppe.get("devices")),
        })
    return out


def speichern(gruppen: list) -> list:
    """Die ganze Gruppenliste ersetzen (die Oberfläche arbeitet so)."""
    bereinigt = []
    for gruppe in gruppen or []:
        if not isinstance(gruppe, dict):
            continue
        name = " ".join(str(gruppe.get("name") or "").split())
        if not name:
            continue
        bereinigt.append({
            "name": name,
            "assistants": _saeubern(gruppe.get("assistants")),
            "devices": _saeubern(gruppe.get("devices")),
        })
    _schreiben({"groups": bereinigt})
    return bereinigt


def fuer_assistent(assistent: str):
    """Gruppe für ein Sprachgerät (Device-ID) oder None.

    Trifft ein Gerät auf mehrere Gruppen, gewinnt die erste - das ist die
    Reihenfolge, die der Eigentümer in der Oberfläche sieht.
    """
    kennung = str(assistent or "").strip()
    if not kennung:
        return None
    for gruppe in liste():
        if kennung in gruppe["assistants"]:
            return gruppe
    return None


def namen_der_assistenten() -> list:
    """Alle Device-IDs, die irgendwo als Assistent eingetragen sind."""
    namen = set()
    for gruppe in liste():
        namen.update(gruppe["assistants"])
    return sorted(namen)


# Alexa-Faehigkeitsname -> Kurzform, wie in localmap.
_CAP_FROM_ALEXA = {
    "powerState": "onoff",
    "active": "onoff",
    "brightness": "brightness",
    "percentage": "brightness",
    "colorName": "color",
    "colorTemperatureName": "colortemp",
    "targetSetpoint": "setpoint",
}


def alle_geraete() -> dict:
    """Alle schaltbaren Geräte: Sprechname -> caps.

    Zwei Quellen, absichtlich beide:
      localmap  - 43 Geräte mit belegtem LOKALEM Ziel (zigbee, wled, sonoff)
      catalog   - 124 Geräte; die übrigen haben nur einen alexa2-Kanal

    Alexa-Kanäle gehen über Amazon (alexa2-Adapter -> Alexa-Cloud -> Gerät).
    Der Befehl wird zwar lokal nach ioBroker geschrieben, die Ausführung
    braucht aber die Cloud, und es gibt keinen Rückkanal (readable: false).
    Auf Wunsch des Eigentuemers (28.10.2026) sind sie trotzdem wählbar - mit
    dem Wissen, dass die Bestätigung schwächer ist und es länger dauert.

    Lokale Ziele haben Vorrang: sie schalten ohne Amazon und lesen zurück.
    """
    import catalog
    import localmap

    bekannt = {}
    for dev in localmap.load().get("devices") or []:
        if dev.get("caps") and dev.get("name"):
            bekannt[dev["name"]] = dict(dev["caps"])

    for dev in catalog.load().get("devices") or []:
        name = dev.get("name") or ""
        if not name or name in bekannt:
            continue
        abbildung = {}
        for c in (dev.get("writable") or []):
            kurz = _CAP_FROM_ALEXA.get(c)
            if kurz and kurz not in abbildung:
                abbildung[kurz] = f"{dev.get('id')}.{c}"
        if abbildung:
            bekannt[name] = abbildung
    return bekannt


def ist_lokal(caps: dict) -> bool:
    """Schaltet dieses Gerät ohne Amazon?"""
    import localmap
    return any(localmap._is_local(dp) for dp in (caps or {}).values())


def geraete_der_gruppe(gruppe) -> list:
    """Geräte einer Gruppe als (Sprechname, caps)-Paare.

    Enthält lokale UND Alexa-Geräte. Ein Tippfehler in der Oberfläche darf
    keinen Fehler beim Sprechen verursachen - unbekannte Namen fallen raus.
    """
    if not isinstance(gruppe, dict):
        return []
    bekannt = alle_geraete()
    out = []
    for name in gruppe.get("devices") or []:
        caps = bekannt.get(name)
        if caps:
            out.append((name, caps))
    return out


if __name__ == "__main__":
    for g in liste():
        print(f"{g['name']}: {len(g['devices'])} Geräte, {len(g['assistants'])} Assistenten")
        for a in g["assistants"]:
            print(f"    Assistent: {a}")
        for d in g["devices"]:
            print(f"    Gerät    : {d}")
