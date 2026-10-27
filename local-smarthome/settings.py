"""Zentrale Einstellung: wo steht ioBroker?

Vorher stand die Adresse als Konstante in drei Dateien
(http://192.168.178.24:8087). Wer dieses Projekt nachbaut, hat eine andere IP -
und muss dann im Quelltext suchen. Deshalb liegt sie jetzt an EINER Stelle und
ist in der Oberflaeche eintragbar.

Reihenfolge (die erste Quelle gewinnt):
  1. settings.json          - was in der Oberflaeche eingetragen wurde
  2. IOBROKER_SIMPLE_API    - Umgebungsvariable, fuer headless Installationen
  3. REFERENZ               - Wert aus dem urspruenglichen Aufbau

Die Datei wird bei jeder Aenderung neu gelesen (mtime-Pruefung), damit eine
Korrektur in der Oberflaeche sofort wirkt - ohne Dienst-Neustart.
"""
from __future__ import annotations

import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
DATEI = os.path.join(HERE, "settings.json")

# Wert aus dem Aufbau dieses Hauses. Nur der letzte Rueckfall, damit ein
# frischer Klon ohne jede Konfiguration nicht ins Leere laeuft.
REFERENZ = "http://192.168.178.24:8087"

MERKER = "iobroker_url"

_cache = {"mtime": None, "wert": None}


def _lesen() -> dict:
    try:
        with open(DATEI, encoding="utf-8") as handle:
            daten = json.load(handle) or {}
    except (OSError, ValueError):
        return {}
    return daten if isinstance(daten, dict) else {}


def _saeubern(url: str) -> str:
    """Adresse vergleichbar machen: kein Schraegstrich am Ende, Schema pruefen."""
    text = str(url or "").strip()
    while text.endswith("/"):
        text = text[:-1]
    return text


def ist_gueltig(url: str) -> bool:
    text = _saeubern(url)
    return text.startswith(("http://", "https://")) and len(text) > len("http://")


def base() -> str:
    """Wirksame ioBroker-Adresse (ohne Schraegstrich am Ende)."""
    try:
        mtime = os.path.getmtime(DATEI)
    except OSError:
        mtime = None
    if _cache["mtime"] == mtime and _cache["wert"]:
        return _cache["wert"]

    aus_datei = _saeubern(_lesen().get(MERKER) or "")
    if ist_gueltig(aus_datei):
        wert = aus_datei
    else:
        aus_env = _saeubern(os.environ.get("IOBROKER_SIMPLE_API") or "")
        wert = aus_env if ist_gueltig(aus_env) else REFERENZ

    _cache["mtime"] = mtime
    _cache["wert"] = wert
    return wert


def quelle() -> str:
    """Woher der Wert kommt - fuer die Anzeige in der Oberflaeche."""
    aus_datei = _saeubern(_lesen().get(MERKER) or "")
    if ist_gueltig(aus_datei):
        return "eigene Einstellung"
    aus_env = _saeubern(os.environ.get("IOBROKER_SIMPLE_API") or "")
    if ist_gueltig(aus_env):
        return "Umgebungsvariable"
    return "Referenzwert (ungeprüft)"


def setzen(url: str) -> str:
    """Adresse speichern. Gibt den gespeicherten Wert zurueck.

    Wirft ValueError bei unbrauchbarer Eingabe - eine kaputte Adresse darf
    nicht gespeichert werden, sonst ist der ganze Sprachweg stumm.
    """
    text = _saeubern(url)
    if not ist_gueltig(text):
        raise ValueError("Adresse muss mit http:// oder https:// beginnen")
    daten = _lesen()
    daten[MERKER] = text
    with open(DATEI, "w", encoding="utf-8") as handle:
        json.dump(daten, handle, ensure_ascii=False, indent=1)
        handle.write("\n")
    _cache["mtime"] = None
    _cache["wert"] = None
    return text
