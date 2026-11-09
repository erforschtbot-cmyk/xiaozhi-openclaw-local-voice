"""Zentrale Einstellung: wo steht ioBroker?

Vorher stand die Adresse als Konstante in drei Dateien
(http://192.168.178.24:8087). Wer dieses Projekt nachbaut, hat eine andere IP -
und muss dann im Quelltext suchen. Deshalb liegt sie jetzt an EINER Stelle und
ist in der Oberflaeche eintragbar.

Reihenfolge (die erste Quelle gewinnt):
  1. settings.json          - was in der Oberflaeche eingetragen wurde
  2. JARVIS_IOBROKER_URL    - zentrale Jarvis-Konfiguration (Container)
  3. IOBROKER_SIMPLE_API    - Umgebungsvariable, fuer headless Installationen
  4. REFERENZ               - Wert aus dem urspruenglichen Aufbau

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


def _env_url() -> str:
    """Adresse aus der Umgebung: JARVIS_IOBROKER_URL hat Vorrang."""
    for name in ("JARVIS_IOBROKER_URL", "IOBROKER_SIMPLE_API"):
        value = _saeubern(os.environ.get(name) or "")
        if ist_gueltig(value):
            return value
    return ""


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
        aus_env = _env_url()
        wert = aus_env if aus_env else REFERENZ

    _cache["mtime"] = mtime
    _cache["wert"] = wert
    return wert


def quelle() -> str:
    """Woher der Wert kommt - fuer die Anzeige in der Oberflaeche."""
    aus_datei = _saeubern(_lesen().get(MERKER) or "")
    if ist_gueltig(aus_datei):
        return "eigene Einstellung"
    aus_env = _env_url()
    if aus_env:
        if _saeubern(os.environ.get("JARVIS_IOBROKER_URL") or ""):
            return "Umgebungsvariable JARVIS_IOBROKER_URL"
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
