#!/usr/bin/env python3
"""Prueft, dass die aktiven Voice-dist-Patches (A und B) gesetzt sind.

Siehe apply-openclaw-voice-dist-patches.py.

  A  Voice-Sprachbestaetigung deaktiviert   (agent-tools.before-tool-call-*.mjs)
  B  Persistenz beide Seiten / v4           (handlers-*.mjs)

Auf dem Referenzhost sind nur A und B aktiv (Stand 2026-10-17).
Patch C (keep-spoken-test) und D (Apostroph) werden hier NICHT geprueft.
Bei Bedarf die entsprechenden Bloecke unten einkommentieren.
"""
import pathlib
import subprocess

root = pathlib.Path(subprocess.run(["npm", "root", "-g"], check=True, text=True,
                                   capture_output=True).stdout.strip())
dist = root / "openclaw" / "dist"

checked = []

# ---- Patch A
confirm = [p for p in sorted(dist.glob("agent-tools.before-tool-call-*.mjs"))
           if "client-voice-confirmation.ts" in p.read_text()]
assert confirm, "Kein Bewaestigungs-Bundle gefunden"
for path in confirm:
    assert "voice-confirmation-disabled-by-owner-v1" in path.read_text(), f"Patch A fehlt: {path}"
checked += confirm

# ---- Patch B
handlers = [p for p in sorted(dist.glob("handlers-*.mjs"))
            if "enqueueRelayVoiceTranscript" in p.read_text()]
assert handlers, "Kein Relay-Handler gefunden"
for path in handlers:
    text = path.read_text()
    assert "voice-persist-split-v4" in text, f"Patch B fehlt: {path}"
    for needle, what in (
        ("voice-persist-split-v4: consult and relay", "Relay-Feld"),
        ("voice-persist-split-v4: provider-direct", "provider-direct-Haken"),
        ("pruneInactiveRelayAgentRuns(relay) === 0", "Reset nur ohne aktiven Run"),
        ("const consultOwns", "Consult-Uebernahme ohne emit-Abbruch"),
        ("voice-persist-split-v4: the consult owns", "Forced-Consult-Merker"),
        ("relay.assistantOwnedByConsult = true;\n\t\t\t\tconst forcedConsult", "Tool-Call-Haken"),
    ):
        assert needle in text, f"Patch B Teil fehlt ({what}): {path}"
checked += handlers

print("Voice-dist-Patches A und B verifiziert:")
for path in checked:
    print(" ", path)
