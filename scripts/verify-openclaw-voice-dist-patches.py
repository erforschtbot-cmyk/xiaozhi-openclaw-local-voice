#!/usr/bin/env python3
"""Prueft, dass beide Voice-dist-Patches aktiv sind (siehe apply-...-dist-patches.py)."""
import pathlib
import subprocess

root = pathlib.Path(subprocess.run(["npm", "root", "-g"], check=True, text=True,
                                   capture_output=True).stdout.strip())
dist = root / "openclaw" / "dist"

confirm = [p for p in sorted(dist.glob("agent-tools.before-tool-call-*.mjs"))
           if "client-voice-confirmation.ts" in p.read_text()]
assert confirm, "Kein Bewaestigungs-Bundle gefunden"
for path in confirm:
    assert "voice-confirmation-disabled-by-owner-v1" in path.read_text(), f"Patch A fehlt: {path}"

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

print("Voice-dist-Patches verifiziert:")
for path in confirm + handlers:
    print(" ", path)
