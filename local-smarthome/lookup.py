#!/usr/bin/env python3
"""Nachschlagewerk fuer den Sprach-Agenten: Geraet suchen, Ziel sehen.

Zweck: Wenn die exakte Erkennung nicht greift (STT-Verhoerer, Beugung,
Zusammenschreibung), soll die KI **gezielt nachschlagen** koennen statt zu
raten oder den langsamen Alexa-Weg zu nehmen.

Rein lesend. Schreibt nichts, schaltet nichts.
"""
from __future__ import annotations

import difflib
import json
import os
import unicodedata

HERE = os.path.dirname(os.path.abspath(__file__))

# Sprechbare Faehigkeiten -> Feldname im Datenpunkt.
# Nur zur Anzeige; geschaltet wird ueber control.py.
_CAP_ORDER = ("onoff", "brightness", "color", "colortemp", "position",
              "temperature", "targetTemperature")


def _norm(text: str) -> str:
    s = unicodedata.normalize("NFKD", str(text)).casefold()
    return "".join(c for c in s if not unicodedata.combining(c))


def _load(name: str) -> list:
    path = os.path.join(HERE, name)
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return []
    devices = data.get("devices", data)
    return devices if isinstance(devices, list) else []


def _local_entries() -> list:
    """Geraete mit belegtem lokalem Ziel (alias/iot-Abgleich)."""
    out = []
    for dev in _load("localmap.json"):
        caps = dev.get("caps") or {}
        if not caps:
            continue
        targets = {cap: caps[cap] for cap in _CAP_ORDER if cap in caps}
        # Restliche Faehigkeiten mitnehmen, damit nichts unsichtbar bleibt.
        for cap, dp in caps.items():
            targets.setdefault(cap, dp)
        out.append({
            "name": dev.get("name"),
            "source": "local",
            "writable": sorted(targets),
            "targets": targets,
            "broken": dev.get("broken") or [],
        })
    return out


def _remote_entries() -> list:
    """Geraete aus dem Alexa-Katalog (alexa2-Datenpunkt, direkt schaltbar)."""
    out = []
    for dev in _load("catalog.json"):
        name = dev.get("name")
        if not name:
            continue
        writable = [c for c in (dev.get("writable") or []) if c]
        out.append({
            "name": name,
            "source": "alexa2",
            "writable": writable,
            "targets": {c: f"{dev.get('id')}.{c}" for c in writable},
            "manufacturer": dev.get("manufacturer") or "",
            "description": dev.get("description") or "",
        })
    return out


def _score(term: str, name: str) -> tuple[float, str]:
    """(Guete 0..100, Grund). Nur exakte und nachvollziehbare Treffer."""
    t, n = _norm(term).strip(), _norm(name).strip()
    if not t or not n:
        return 0.0, ""
    if t == n:
        return 100.0, "exakt"
    # Einzelwort im Namen enthalten ("monitor" -> "Monitorlicht")
    if t in n.split() or n in t.split():
        return 92.0, "wort"
    if len(t) >= 4 and t in n:
        return 88.0, "teil"
    # STT-Verhoerer ("monitorarus" -> "monitor"): Anfang muss stimmen.
    for size in range(min(len(n), len(t)), 3, -1):
        if t.startswith(n[:size]) or n.startswith(t[:size]):
            share = size / max(len(n), len(t))
            if share >= 0.6:
                return 70.0 + 20.0 * share, "anfang"
    ratio = difflib.SequenceMatcher(None, n, t).ratio()
    if ratio >= 0.75:
        return 60.0 + 20.0 * ratio, "aehnlich"
    return 0.0, ""


def search(term: str, limit: int = 8) -> dict:
    """Geraete suchen. Liefert Kandidaten mit ihren schaltbaren Zielen."""
    entries = _local_entries() + _remote_entries()
    scored = []
    for entry in entries:
        score, why = _score(term, entry["name"])
        if score > 0:
            scored.append((score, why, entry))
    if not scored:
        return {"ok": True, "query": term, "found": 0, "candidates": [],
                "hint": "Kein Geraet passt. Andere Schreibweise versuchen."}
    scored.sort(key=lambda item: (-item[0], len(item[2]["name"])))
    top = scored[0][0]
    candidates = []
    for score, why, entry in scored[:limit]:
        # Nur echte Treffer zeigen, keine schwache Streuung.
        if score < max(60.0, top - 25.0):
            continue
        item = {
            "name": entry["name"],
            "source": entry["source"],
            "writable": entry["writable"],
            "targets": entry["targets"],
            "match": why,
            "score": round(score, 1),
        }
        if entry.get("manufacturer"):
            item["manufacturer"] = entry["manufacturer"]
        if entry.get("description"):
            item["description"] = entry["description"]
        if entry.get("broken"):
            item["broken"] = entry["broken"]
        candidates.append(item)
    return {
        "ok": True,
        "query": term,
        "found": len(candidates),
        "candidates": candidates,
        "hint": (
            "Zum Schalten den Geraetenamen exakt verwenden, "
            "z. B. 'Monitor aus'. Kein Raten bei mehreren Treffern."
        ),
    }


def main(argv: list[str]) -> int:
    term = " ".join(argv).strip()
    if not term:
        print(json.dumps({"ok": False, "reason": "kein-suchbegriff"}))
        return 2
    print(json.dumps(search(term), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main(sys.argv[1:]))
