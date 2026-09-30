#!/usr/bin/env python3
"""Prueft, dass alle vier Voice-dist-Patches aktiv sind.

Siehe apply-openclaw-voice-dist-patches.py.

  A  Voice-Sprachbestaetigung deaktiviert   (agent-tools.before-tool-call-*.mjs)
  B  Persistenz beide Seiten                (handlers-*.mjs)
  C  gesprochener Satz bleibt sichtbar      (builtin-openclaw-*.mjs)
  D  Apostroph I'll (ASCII)                 (capability-catalog.js,
                                             realtime-quicksilver-delegation-controller-*.mjs)
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
    assert "voice-no-tool-both-sides-v1" in text, f"Patch B fehlt: {path}"
    for needle, what in (
        ("assistantOwnedByConsult: false", "Relay-Feld"),
        ('outcome !== "control"', "provider-direct-Haken"),
        ("pruneInactiveRelayAgentRuns(relay) === 0", "Reset nur ohne aktiven Run"),
        ("const consultOwns", "Consult-Uebernahme ohne emit-Abbruch"),
        ("interimAck", "Zwischenansage-Filter"),
    ):
        assert needle in text, f"Patch B Teil fehlt ({what}): {path}"
checked += handlers

# ---- Patch C
builtin = [p for p in sorted(dist.glob("builtin-openclaw-*.mjs"))
           if "resolveOrphanRepairPlan" in p.read_text()]
assert builtin, "Kein Orphan-Repair-Bundle gefunden"
for path in builtin:
    text = path.read_text()
    assert "keep-spoken-test" in text, f"Patch C fehlt: {path}"
    assert 'keepSpokenProvenance.kind === "realtime_voice"' in text, f"Patch C Teil fehlt: {path}"
checked += builtin

# ---- Patch D
apostrophe = sorted(dist.glob("**/capability-catalog.js")) + \
    sorted(dist.glob("realtime-quicksilver-delegation-controller-*.mjs"))
apostrophe = [p for p in apostrophe if p.exists()
              and "buildRealtimeVoiceAgentControlSpeechMessage" in p.read_text()]
assert apostrophe, "Keine Apostroph-Datei gefunden"
for path in apostrophe:
    text = path.read_text()
    assert 'buildRealtimeVoiceAgentControlSpeechMessage("I\u2019ll check that request.")' not in text, \
        f"Patch D fehlt (noch U+2019): {path}"
    assert 'buildRealtimeVoiceAgentControlSpeechMessage("I\'ll check that request.")' in text, \
        f"Patch D fehlt: {path}"
checked += apostrophe

print("Voice-dist-Patches A-D verifiziert:")
for path in checked:
    print(" ", path)
