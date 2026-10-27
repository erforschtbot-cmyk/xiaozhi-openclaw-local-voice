"""Vom Eigentuemer vergebene Namen und Raumzuordnungen.

Die Geraetenamen kommen aus zwei Quellen, und beide sind unbrauchbar, sobald
Alexa sie verbogen hat: im Katalog stehen Reste wie '"Licht Arbeitszimmer"off'
oder schlicht 'ON'. Der Eigentuemer soll Namen vergeben koennen wie in der
Alexa-App - ohne dass ein Neuaufbau des Katalogs sie ueberschreibt.

Deshalb liegen sie in einer eigenen Datei (names_overrides.json), die kein
Generator anfasst. Sie wird beim Laden ueber die Geraeteliste gelegt:

    dev["name"]      = Anzeigename (so wird gesprochen)
    dev["baseName"]  = urspruenglicher Name (bleibt nachvollziehbar)
    dev["room"]      = Raum, falls zugeordnet

Wichtig: Der Abgleich in resolver.py arbeitet auf `name`. Ein neuer Name wirkt
deshalb sofort im Sprachweg, ohne dass dort etwas geaendert werden muss.
"""
from __future__ import annotations

import json
import os
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
OVERRIDES = os.path.join(HERE, "names_overrides.json")

_EMPTY = {"names": {}, "rooms": {}, "targets": {}}


def _read() -> dict:
    try:
        with open(OVERRIDES, encoding="utf-8") as handle:
            data = json.load(handle) or {}
    except (OSError, ValueError):
        return dict(_EMPTY)
    if not isinstance(data, dict):
        return dict(_EMPTY)
    data.setdefault("names", {})
    data.setdefault("rooms", {})
    data.setdefault("targets", {})
    return data


def load() -> dict:
    """Aktueller Stand der Overrides."""
    return _read()


def _write(data: dict) -> None:
    """Atomar schreiben: kein halber Zustand bei Absturz oder Stromausfall."""
    os.makedirs(os.path.dirname(OVERRIDES), exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(OVERRIDES), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=1)
            handle.write("\n")
        os.replace(tmp, OVERRIDES)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def set_name(device_id: str, name: str) -> dict:
    """Neuen Sprechnamen setzen. Leerer Name loescht die Zuordnung."""
    data = _read()
    key = str(device_id or "").strip()
    if not key:
        raise ValueError("device_id fehlt")
    clean = " ".join(str(name or "").split())
    if clean:
        data["names"][key] = clean
    else:
        data["names"].pop(key, None)
    _write(data)
    return data


def set_room(device_id: str, room: str) -> dict:
    """Geraet einem Raum zuordnen. Leerer Raum loescht die Zuordnung."""
    data = _read()
    key = str(device_id or "").strip()
    if not key:
        raise ValueError("device_id fehlt")
    clean = " ".join(str(room or "").split())
    if clean:
        data["rooms"][key] = clean
    else:
        data["rooms"].pop(key, None)
    _write(data)
    return data


def set_target(ident: str, datapoint: str, cap: str = "") -> dict:
    """Anderen Datenpunkt als Schaltziel vorgeben.

    Gedacht fuer Geraete, deren automatisch ermitteltes Ziel falsch oder
    unbrauchbar ist (Alexa-Kanal statt lokalem Aktor, defekter Alias). `cap`
    bestimmt den Wertetyp: bool-artig (onoff/powerState/active) oder Zahl
    (brightness/percentage). Leerer Datenpunkt loescht die Vorgabe.
    """
    data = _read()
    key = str(ident or "").strip()
    if not key:
        raise ValueError("Kennung fehlt")
    dp = str(datapoint or "").strip()
    if dp:
        entry = {"datapoint": dp}
        clean_cap = str(cap or "").strip()
        if clean_cap:
            entry["cap"] = clean_cap
        data["targets"][key] = entry
    else:
        data["targets"].pop(key, None)
    _write(data)
    return data


def target_for(*idents):
    """Vorgegebenes Ziel als {"datapoint": ..., "cap": ...} oder None.

    Nimmt mehrere Kennungen entgegen und probiert sie der Reihe nach. Noetig,
    weil eine Vorgabe je nach Herkunft unter der Datenpunkt-id ODER unter dem
    Sprechnamen stehen kann. Ohne diese Reihenfolge fanden Aufrufer mit der
    jeweils anderen Kennung die Vorgabe nicht (belegt 27.10.2026: Ziel wurde
    geschrieben, die Weg-Kennzeichnung blieb aber "alexa2-cloud").
    """
    targets = _read()["targets"]
    for ident in idents:
        if not ident:
            continue
        entry = targets.get(str(ident).strip())
        if isinstance(entry, str) and entry:
            return {"datapoint": entry}
        if isinstance(entry, dict) and entry.get("datapoint"):
            return entry
    return None


def apply(devices: list) -> list:
    """Overrides ueber eine Geraeteliste legen (in place, Liste zurueck).

    Zugeordnet wird ueber `id`; fehlt die, ueber den urspruenglichen Namen.
    """
    data = _read()
    names = data["names"]
    rooms = data["rooms"]
    for dev in devices:
        if not isinstance(dev, dict):
            continue
        ident = dev.get("id") or dev.get("name")
        base = dev.get("baseName") or dev.get("name")
        if base and not dev.get("baseName"):
            dev["baseName"] = base
        if ident in names:
            dev["name"] = names[ident]
        if ident in rooms:
            dev["room"] = rooms[ident]
    return devices
