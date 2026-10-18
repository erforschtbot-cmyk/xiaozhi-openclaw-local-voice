#!/usr/bin/env python3
"""Prueft, dass die Jarvis-Gateway-Weiche vollstaendig gesetzt ist.

Siehe apply-jarvis-smart-home-router.py.

  W1  Weiche am Consult-Eingang            (handlers-*.mjs, jarvis-safe-router-v1)
  W2  Abschluss ohne Agentenlauf           (handlers-*.mjs, jarvis-direct-completion-v2)

Zusaetzlich: Die Router-Datei muss vorhanden sein, und ihre Tests muessen
gruen sein (kein Geraetezugriff, alle Schreibvorgaenge sind Attrappen).
"""
from __future__ import annotations

import pathlib
import subprocess
import sys

BRIDGE_DIR = pathlib.Path("/home/openclaw/.local/share/jarvis-realtime-bridge")

root = pathlib.Path(
    subprocess.run(["npm", "root", "-g"], check=True, text=True,
                   capture_output=True).stdout.strip()
)
dist = root / "openclaw" / "dist"

router = BRIDGE_DIR / "smart-home-router.mjs"
assert router.is_file(), f"Router fehlt: {router}"
tests = BRIDGE_DIR / "test-smart-home-router.mjs"
assert tests.is_file(), f"Testdatei fehlt: {tests}"

handlers = [p for p in sorted(dist.glob("handlers-*.mjs"))
            if "enqueueRelayVoiceTranscript" in p.read_text()]
assert handlers, "Kein Realtime-Relay-Handler gefunden."

checked = []
for path in handlers:
    text = path.read_text()
    assert "jarvis-safe-router-v1" in text, f"W1 fehlt: {path}"
    assert "jarvis-direct-completion-v2" in text, f"W2 fehlt: {path}"
    assert f"file://{BRIDGE_DIR}/smart-home-router.mjs" in text, \
        f"Router-Pfad fehlt: {path}"
    assert "routeSmartHome({" in text, f"Weichenaufruf fehlt: {path}"
    assert "owner.directResultReady = true;" in text, f"Direktabschluss fehlt: {path}"
    assert "owner.directResultReady = false;" in text, f"Merker-Reset fehlt: {path}"
    checked.append(path)

result = subprocess.run(["node", "--test", str(tests)],
                        capture_output=True, text=True, cwd=BRIDGE_DIR)
if result.returncode != 0:
    sys.stdout.write(result.stdout[-2000:])
    sys.stderr.write(result.stderr[-2000:])
    raise SystemExit("Router-Tests fehlgeschlagen.")
summary = [ln for ln in result.stdout.splitlines() if ln.startswith("ℹ pass")
           or ln.startswith("ℹ fail")]

print("Jarvis-Gateway-Weiche W1 und W2 verifiziert:")
for path in checked:
    print(" ", path)
print("Router-Tests:", ", ".join(summary))
print("Hinweis: Ein Treffer im Log sieht so aus:")
print('  [jarvis-fast-route] accepted device="Monitor" action=aus http=200 elapsedMs=27')
