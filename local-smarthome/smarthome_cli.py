#!/usr/bin/env python3
"""Einmaliger Aufruf fuer den Sprachpfad: Satz rein, JSON raus.

Wird von local-voice.mjs per execFileSync aufgerufen. Bewusst ein eigener
Prozess: der Node-Helper soll nicht von einem dauerhaft laufenden Python
abhaengen, und ein Absturz hier darf den Sprachdienst nicht mitnehmen.
"""
from __future__ import annotations

import json
import os
import sys


def main() -> int:
    if len(sys.argv) < 2:
        print(json.dumps({"ok": False, "reason": "kein-satz"}))
        return 2
    text = " ".join(sys.argv[1:])
    # SMARTHOME_DRY_RUN=1 loest nur auf und schreibt nichts. Fuer Tests am
    # Sprachpfad, ohne ein Geraet im Haus zu schalten.
    dry = os.environ.get("SMARTHOME_DRY_RUN") == "1"
    # Device-ID des Sprachgeraets, das das Wachwort gehoert hat. Nur damit
    # laesst sich eine Gruppe zuordnen ("Licht an" im Wohnzimmer). Der Server
    # setzt die Variable (server.py, OPENCLAW_VOICE_DEVICE_ID).
    assistant = os.environ.get("OPENCLAW_VOICE_DEVICE_ID") or None
    try:
        import control
        result = control.handle(text, dry_run=dry,
                                assistant=assistant)
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "reason": f"fehler:{type(exc).__name__}"}))
        return 1
    # Nur die Felder, die der Sprachpfad braucht. Keine Datenpunkt-Dumps.
    # Kurze Sprechbestaetigung. Bewusst schlicht und ohne Behauptung, die die
    # Daten nicht decken: nach einem Gruppenbefehl kann der Adapter den
    # Einzelzustand nicht bestaetigen, dann wird auch nichts behauptet.
    # Bei einer Temperaturfrage ist die Antwort der Messwert selbst.
    confirmation = None
    if result.get("query") or result.get("answer"):
        # Frage oder Sollwert: der fertige Sprechtext steht schon da.
        confirmation = result.get("answer")
    elif result.get("ok"):
        who = result.get("room") or result.get("device")
        if who:
            if result.get("action") == "temperature" and result.get("value") is not None:
                # Sollwert: "Klimaanlage auf 22 Grad gestellt." - "geschaltet"
                # waere fuer eine Temperatur das falsche Wort.
                wert = result["value"]
                try:
                    zahl = f"{float(wert):g}"
                except (TypeError, ValueError):
                    zahl = str(wert)
                confirmation = f"{who} auf {zahl} Grad gestellt."
            elif result.get("failed"):
                confirmation = f"{who} nur teilweise geschaltet."
            elif result.get("stale_warning"):
                confirmation = f"{who} geschaltet."
            elif any(w.get("confirm") == "unbestaetigt"
                     for w in (result.get("written") or [])):
                confirmation = f"{who} geschaltet, aber nicht bestätigt."
            else:
                confirmation = f"{who} geschaltet."

    out = {
        "confirmation": confirmation,
        "answer": result.get("answer"),
        "query": bool(result.get("query")),
        "value": result.get("value"),
        "detail": result.get("detail"),
        "ok": bool(result.get("ok")),
        "reason": result.get("reason"),
        "room": result.get("room"),
        "device": result.get("device"),
        "action": result.get("action"),
        "targets": result.get("targets"),
        "group": result.get("group"),
        "datapoint": result.get("datapoint"),
        "failed": result.get("failed"),
        "stale_warning": result.get("stale_warning"),
    }
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
