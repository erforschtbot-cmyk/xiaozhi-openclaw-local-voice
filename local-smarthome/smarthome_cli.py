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
    try:
        import control
        result = control.handle(text, prefer_local=True, dry_run=dry)
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "reason": f"fehler:{type(exc).__name__}"}))
        return 1
    # Nur die Felder, die der Sprachpfad braucht. Keine Datenpunkt-Dumps.
    # Kurze Sprechbestaetigung. Bewusst schlicht und ohne Behauptung, die die
    # Daten nicht decken: nach einem Gruppenbefehl kann der Adapter den
    # Einzelzustand nicht bestaetigen, dann wird auch nichts behauptet.
    confirmation = None
    if result.get("ok"):
        who = result.get("room") or result.get("device")
        if who:
            if result.get("failed"):
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
        "ok": bool(result.get("ok")),
        "route": result.get("route"),
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
