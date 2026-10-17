#!/usr/bin/env python3
"""Wendet die aktiven OpenClaw-dist-Patches fuer den Jarvis-Voice-Pfad an.

Patch A - agent-tools.before-tool-call-*.mjs
    Deaktiviert die zusaetzliche Talk-Sprachbestaetigung vollstaendig
    (voice-confirmation-disabled-by-owner-v1).

Patch B - handlers-*.mjs  (Marker voice-no-tool-both-sides-v1)
    Ziel: Im OHNE-Tool-Fall stehen beide Texte in der Session (Frage + Antwort).
    Im MIT-Tool-Fall schreibt der Consult die Antwort; der Relay schweigt, damit
    nichts doppelt entsteht.

    Umsetzung (drei Stellen):
      B1  Relay-Feld assistantOwnedByConsult
      B2  Merker setzen im provider-direct-Haken (handleDelegationInput)
      B3  Transkript-Gate: Zwischenansage verwerfen; Assistant ueberspringen,
          wenn ein Consult den Turn besitzt (Merker danach verbrauchen);
          Sicherheitsnetz-Reset bei neuer Frage nur ohne aktiven Run

Patch C - builtin-openclaw-*.mjs  (Marker keep-spoken-test)
    Haelt den eigenen gesprochenen Satz in der sichtbaren Session. Ohne den
    Ausstieg haengt die Orphan-Repair den Leaf auf den letzten Assistant
    zurueck; die gesprochene Aeusserung (provenance.kind=realtime_voice) bleibt
    dann auf einem Seitenast und verschwindet.

Patch D - capability-catalog.js + realtime-quicksilver-delegation-controller-*.mjs
    Setzt den Apostroph in der Zwischenansage auf ASCII: I’ll -> I'll
    (rein kosmetisch, kein Sprachwechsel).

Alle Patches liegen in kompilierten dist-Dateien und werden von jedem
OpenClaw-Update ueberschrieben. Nach jedem Update erneut anwenden.
"""
from __future__ import annotations

import datetime
import pathlib
import subprocess
import sys

# ---------------------------------------------------------------- Patch A
ANCHOR_A = 'function resolveClientVoiceToolConfirmationPolicy(params, consume) {\n\tif (!params.agentId || !params.voiceSessionId) return { allowed: true };\n'
PATCH_A = 'function resolveClientVoiceToolConfirmationPolicy(params, consume) {\n\t// voice-confirmation-disabled-by-owner-v1 (reapply after update)\n\treturn { allowed: true };\n\tif (!params.agentId || !params.voiceSessionId) return { allowed: true };\n'
MARK_A = 'voice-confirmation-disabled-by-owner-v1'

# ---------------------------------------------------------------- Patch B
MARK_B = 'voice-persist-split-v4'

# B1: Relay-Feld
B1_ANCHOR = '\t\tvoiceTranscriptSeq: 0,\n\t\tvoiceTranscriptQueue: VOICE_TRANSCRIPT_QUEUE_POLICY.createQueue(),\n'
B1_REPLACE = '\t\tvoiceTranscriptSeq: 0,\n\t\t// voice-persist-split-v4: consult and relay must not persist the same answer.\n\t\tassistantOwnedByConsult: false,\n\t\tvoiceTranscriptQueue: VOICE_TRANSCRIPT_QUEUE_POLICY.createQueue(),\n'

# B2: provider-direct-Haken
B2_ANCHOR = '\t\t...runControl.handleDelegationInput ? { handleDelegationInput: (text, respond) => {\n\t\t\tconst relay = getActiveRelay();\n\t\t\tif (!relay) return "control";\n\t\t\treturn runControl.handleDelegationInput(text, (message) => {\n\t\t\t\tif (getActiveRelay() === relay) respond(message);\n\t\t\t});\n\t\t} } : {},\n'
B2_REPLACE = '\t\t...runControl.handleDelegationInput ? { handleDelegationInput: (text, respond) => {\n\t\t\tconst relay = getActiveRelay();\n\t\t\tif (!relay) return "control";\n\t\t\tconst outcome = runControl.handleDelegationInput(text, (message) => {\n\t\t\t\tif (getActiveRelay() === relay) respond(message);\n\t\t\t});\n\t\t\t// voice-persist-split-v4: provider-direct has no visible consult tool call.\n\t\t\tif (outcome !== "control") relay.assistantOwnedByConsult = true;\n\t\t\treturn outcome;\n\t\t} } : {},\n'

# B3: Transkript-Gate
B3_ANCHOR = '\t\t\tif (final && !enqueueRelayVoiceTranscript(relay, role, text)) return;\n'
B3_REPLACE = '\t\t\tif (final) {\n\t\t\t\tif (role === "user" && pruneInactiveRelayAgentRuns(relay) === 0) relay.assistantOwnedByConsult = false;\n\t\t\t\tif (role === "assistant") {\n\t\t\t\t\tconst spoken = String(text ?? "").trim();\n\t\t\t\t\tconst interimAck = /^(i[\'\\u2019]?ll check that request\\.?|ich pr\\u00fcfe das( kurz)?\\.?|einen moment( bitte)?\\.?)$/i.test(spoken);\n\t\t\t\t\tconst consultOwns = relay.assistantOwnedByConsult || pruneInactiveRelayAgentRuns(relay) > 0;\n\t\t\t\t\tif (!interimAck && relay.assistantOwnedByConsult) relay.assistantOwnedByConsult = false;\n\t\t\t\t\t// This gate controls persistence only. The event must still reach the device.\n\t\t\t\t\tif (!interimAck && !consultOwns && !enqueueRelayVoiceTranscript(relay, role, text)) return;\n\t\t\t\t} else if (!enqueueRelayVoiceTranscript(relay, role, text)) return;\n\t\t\t}\n'

B4_ANCHOR = '\t\tconst itemId = `forced-consult-item-${randomUUID()}`;\n'
B4_REPLACE = '\t\tconst itemId = `forced-consult-item-${randomUUID()}`;\n\t\t// voice-persist-split-v4: the consult owns the assistant transcript.\n\t\tsession.assistantOwnedByConsult = true;\n'

B5_ANCHOR = '\t\t\tif (toolCall.name === "openclaw_agent_consult") {\n'
B5_REPLACE = '\t\t\tif (toolCall.name === "openclaw_agent_consult") {\n\t\t\t\trelay.assistantOwnedByConsult = true;\n'

B_EDITS = (
    ("relay-field", B1_ANCHOR, B1_REPLACE),
    ("delegation-hook", B2_ANCHOR, B2_REPLACE),
    ("transcript-gate", B3_ANCHOR, B3_REPLACE),
    ("forced-consult", B4_ANCHOR, B4_REPLACE),
    ("tool-call-hook", B5_ANCHOR, B5_REPLACE),
)
B_NEEDLES = (
    ("voice-persist-split-v4: consult and relay", "Relay-Feld"),
    ("voice-persist-split-v4: provider-direct", "provider-direct-Haken"),
    ("pruneInactiveRelayAgentRuns(relay) === 0", "Reset nur ohne aktiven Run"),
    ("const consultOwns", "Consult-Uebernahme ohne emit-Abbruch"),
    ("voice-persist-split-v4: the consult owns", "Forced-Consult-Merker"),
    ("relay.assistantOwnedByConsult = true;\n\t\t\t\tconst forcedConsult", "Tool-Call-Haken"),
)
# ---------------------------------------------------------------- Patch C
MARK_C = 'keep-spoken-test'
ANCHOR_C = (
    '\tconst candidate = findTrailingMessageEntryForOrphanRepair(params.sessionManager);\n'
    '\tif (!candidate || !isUserSessionMessageEntry(candidate.messageEntry)) return;\n'
)
PATCH_C = (
    ANCHOR_C
    + '\t// keep-spoken-test (Owner-Test 2026-09-27)\n'
    + '\t// Die eigene gesprochene Sprache kommt als provenance.kind=realtime_voice.\n'
    + '\t// Ohne diesen Ausstieg haengt die Orphan-Repair den Leaf auf den letzten\n'
    + '\t// Assistant zurueck — der gesprochene Satz bleibt dann auf einem Seitenast\n'
    + '\t// und verschwindet aus der sichtbaren Instanz. Zum Zuruecksetzen diese\n'
    + '\t// if-Zeile einfach entfernen.\n'
    + '\tconst keepSpokenProvenance = candidate.messageEntry.message.provenance;\n'
    + '\tif (keepSpokenProvenance && keepSpokenProvenance.kind === "realtime_voice") return;\n'
)

# ---------------------------------------------------------------- Patch D
MARK_D = '"I\'ll check that request."'
ANCHOR_D = 'buildRealtimeVoiceAgentControlSpeechMessage("I\u2019ll check that request.")'
PATCH_D = 'buildRealtimeVoiceAgentControlSpeechMessage("I\'ll check that request.")'


def patch(path: pathlib.Path, anchor: str, replacement: str, marker: str, label: str) -> str:
    text = path.read_text()
    if marker in text:
        return "already"
    if anchor not in text:
        raise SystemExit(f"{label}: Anker nicht gefunden in {path} — Layout hat sich geaendert.")
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    path.with_name(path.name + f".bak-{label}-{stamp}").write_text(text)
    path.write_text(text.replace(anchor, replacement, 1))
    if marker not in path.read_text():
        raise SystemExit(f"{label}: Verifikation nach dem Schreiben fehlgeschlagen: {path}")
    return "changed"


def patch_handlers(path: pathlib.Path) -> str:
    if MARK_B in path.read_text():
        return "already"
    text = path.read_text()
    for label, anchor, _replacement in B_EDITS:
        if anchor not in text:
            raise SystemExit(f"persist-split-v4/{label}: Anker nicht gefunden in {path} — "
                             "Layout hat sich geaendert.")
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    path.with_name(path.name + f".bak-both-sides-{stamp}").write_text(text)
    for _label, anchor, replacement in B_EDITS:
        text = text.replace(anchor, replacement, 1)
    path.write_text(text)
    reread = path.read_text()
    if MARK_B not in reread:
        raise SystemExit(f"persist-split-v4: Verifikation nach dem Schreiben fehlgeschlagen: {path}")
    for needle, what in B_NEEDLES:
        if needle not in reread:
            raise SystemExit(f"persist-split-v4: Teil fehlt ({what}): {path}")
    return "changed"


def main() -> int:
    root = pathlib.Path(subprocess.run(["npm", "root", "-g"], check=True, text=True,
                                       capture_output=True).stdout.strip())
    dist = root / "openclaw" / "dist"
    result = {}

    tool_files = sorted(dist.glob("agent-tools.before-tool-call-*.mjs"))
    if not tool_files:
        raise SystemExit(f"Kein OpenClaw-Bundle unter {dist}")
    for path in tool_files:
        if "client-voice-confirmation.ts" in path.read_text():
            result[f"confirm:{path.name}"] = patch(path, ANCHOR_A, PATCH_A, MARK_A, "voice-confirm-off")

    handler_files = sorted(dist.glob("handlers-*.mjs"))
    for path in handler_files:
        if "enqueueRelayVoiceTranscript" in path.read_text():
            result[f"transcript:{path.name}"] = patch_handlers(path)

    builtin_files = [p for p in sorted(dist.glob("builtin-openclaw-*.mjs"))
                     if "resolveOrphanRepairPlan" in p.read_text()]
    for path in builtin_files:
        result[f"keep-spoken:{path.name}"] = patch(path, ANCHOR_C, PATCH_C, MARK_C, "keep-spoken")

    apostrophe_files = sorted(dist.glob("**/capability-catalog.js")) + \
        sorted(dist.glob("realtime-quicksilver-delegation-controller-*.mjs"))
    apostrophe_files = [p for p in apostrophe_files
                        if "buildRealtimeVoiceAgentControlSpeechMessage" in p.read_text()]
    for path in apostrophe_files:
        text = path.read_text()
        if ANCHOR_D in text or MARK_D in text:
            result[f"apostrophe:{path.name}"] = patch(path, ANCHOR_D, PATCH_D, MARK_D, "apostrophe")

    if not result:
        raise SystemExit("Keine passende OpenClaw-Implementierung gefunden.")
    for key, value in sorted(result.items()):
        print(f"{key}: {value}")
    print("Neustart noetig: systemctl --user restart openclaw-gateway.service")
    return 0


if __name__ == "__main__":
    sys.exit(main())
