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

Beide Patches liegen in kompilierten dist-Dateien und werden von jedem
OpenClaw-Update ueberschrieben. Nach jedem Update erneut anwenden.
"""
from __future__ import annotations

import datetime
import pathlib
import subprocess
import sys

# ---------------------------------------------------------------- Patch A
ANCHOR_A = (
    'function resolveClientVoiceToolConfirmationPolicy(params, consume) {\n'
    '\tif (!params.agentId || !params.voiceSessionId) return { allowed: true };\n'
)
PATCH_A = (
    ANCHOR_A
    + '\t// voice-confirmation-disabled-by-owner-v1\n'
    + '\t// The owner explicitly disabled the additional Talk voice confirmation.\n'
    + '\t// No command, argument, punctuation, quoting, or wording filter is used.\n'
    + '\treturn { allowed: true };\n'
)
MARK_A = 'voice-confirmation-disabled-by-owner-v1'

# ---------------------------------------------------------------- Patch B
MARK_B = 'voice-no-tool-both-sides-v1'

# B1: Relay-Feld
B1_ANCHOR = (
    '\t\tvoiceTranscriptSeq: 0,\n'
    '\t\tvoiceTranscriptQueue: VOICE_TRANSCRIPT_QUEUE_POLICY.createQueue(),\n'
)
B1_REPLACE = (
    '\t\tvoiceTranscriptSeq: 0,\n'
    '\t\t// Wird gesetzt, sobald in diesem Turn ein Consult startet. Dann schreibt der\n'
    '\t\t// Consult die Assistant-Antwort und der Relay schweigt (Tool-Fall). Ohne\n'
    '\t\t// Consult schreibt der Relay die Antwort selbst (Ohne-Tool-Fall).\n'
    '\t\tassistantOwnedByConsult: false,\n'
    '\t\tvoiceTranscriptQueue: VOICE_TRANSCRIPT_QUEUE_POLICY.createQueue(),\n'
)

# B2: provider-direct-Haken
B2_ANCHOR = (
    '\t\t...runControl.handleDelegationInput ? { handleDelegationInput: (text, respond) => {\n'
    '\t\t\tconst relay = getActiveRelay();\n'
    '\t\t\tif (!relay) return "control";\n'
    '\t\t\treturn runControl.handleDelegationInput(text, (message) => {\n'
    '\t\t\t\tif (getActiveRelay() === relay) respond(message);\n'
    '\t\t\t});\n'
    '\t\t} } : {},\n'
)
B2_REPLACE = (
    '\t\t...runControl.handleDelegationInput ? { handleDelegationInput: (text, respond) => {\n'
    '\t\t\tconst relay = getActiveRelay();\n'
    '\t\t\tif (!relay) return "control";\n'
    '\t\t\tconst outcome = runControl.handleDelegationInput(text, (message) => {\n'
    '\t\t\t\tif (getActiveRelay() === relay) respond(message);\n'
    '\t\t\t});\n'
    '\t\t\t// voice-no-tool-both-sides-v1\n'
    '\t\t\t// provider-direct: der Consult kommt nicht als Tool-Call an. Merker hier\n'
    '\t\t\t// setzen, damit der Relay die Assistant-Antwort nicht doppelt schreibt.\n'
    '\t\t\tif (outcome !== "control") relay.assistantOwnedByConsult = true;\n'
    '\t\t\treturn outcome;\n'
    '\t\t} } : {},\n'
)

# B3: Transkript-Gate
B3_ANCHOR = '\t\t\tif (final && !enqueueRelayVoiceTranscript(relay, role, text)) return;\n'
B3_REPLACE = (
    '\t\t\t// voice-no-tool-both-sides-v1\n'
    '\t\t\t// Ziel des Owners: Im Ohne-Tool-Fall stehen BEIDE Texte in der Session\n'
    '\t\t\t// (seine Frage und die KI-Antwort). Im Tool-Fall schreibt der Consult die\n'
    '\t\t\t// Antwort; der Relay schweigt dann, damit nichts doppelt entsteht.\n'
    '\t\t\t//\n'
    '\t\t\t// Reihenfolge (provider-direct): der Consult startet VOR dem User-Text und\n'
    '\t\t\t// setzt assistantOwnedByConsult. Deshalb wird der Merker NICHT beim\n'
    '\t\t\t// User-Text geloescht, sondern erst verbraucht, wenn eine\n'
    '\t\t\t// Assistant-Zeile darueber entschieden hat. Danach ist der naechste Turn\n'
    '\t\t\t// wieder frei fuer den Relay.\n'
    '\t\t\tif (final) {\n'
    '\t\t\t\t// Sicherheitsnetz: leckt der Merker aus einem vorigen Tool-Turn herein,\n'
    '\t\t\t\t// wird er bei einer neuen Frage geloescht — aber nur, wenn gerade KEIN\n'
    '\t\t\t\t// Consult laeuft. (provider-direct startet den Consult vor der Frage,\n'
    '\t\t\t\t// deshalb ist der Merker im Tool-Turn zu diesem Zeitpunkt gewollt true.)\n'
    '\t\t\t\tif (role === "user" && pruneInactiveRelayAgentRuns(relay) === 0) relay.assistantOwnedByConsult = false;\n'
    '\t\t\t\tif (role === "assistant") {\n'
    '\t\t\t\t\tconst spoken = String(text ?? "").trim();\n'
    '\t\t\t\t\tconst interimAck = /^(i[\'\\u2019]?ll check that request\\.?|ich pr\\u00fcfe das( kurz)?\\.?|einen moment( bitte)?\\.?)$/i.test(spoken);\n'
    '\t\t\t\t\tconst consultOwns = relay.assistantOwnedByConsult;\n'
    '\t\t\t\t\t// Merker NUR bei einer echten Antwort verbrauchen, nicht schon bei\n'
    '\t\t\t\t\t// der Zwischenansage. Sonst war der Merker weg, wenn die richtige\n'
    '\t\t\t\t\t// Antwort kam, und der Relay schrieb sie zusaetzlich (Doppelung).\n'
    '\t\t\t\t\tif (!interimAck && consultOwns) relay.assistantOwnedByConsult = false;\n'
    '\t\t\t\t\t// WICHTIG: nur das Persistieren auslassen — der emit()-Block unten\n'
    '\t\t\t\t\t// muss IMMER laufen. Ein return hier liess das Geraet im\n'
    '\t\t\t\t\t// "spricht"-Zustand haengen und machte den Ton abgehackt.\n'
    '\t\t\t\t\tif (!interimAck && !consultOwns) {\n'
    '\t\t\t\t\t\tif (!enqueueRelayVoiceTranscript(relay, role, text)) return;\n'
    '\t\t\t\t\t}\n'
    '\t\t\t\t} else {\n'
    '\t\t\t\t\tif (!enqueueRelayVoiceTranscript(relay, role, text)) return;\n'
    '\t\t\t\t}\n'
    '\t\t\t}\n'
)

B_EDITS = (
    ("field", B1_ANCHOR, B1_REPLACE),
    ("delegation-hook", B2_ANCHOR, B2_REPLACE),
    ("transcript-gate", B3_ANCHOR, B3_REPLACE),
)
B_NEEDLES = (
    ("assistantOwnedByConsult: false", "Relay-Feld"),
    ("outcome !== \"control\"", "provider-direct-Haken"),
    ("pruneInactiveRelayAgentRuns(relay) === 0", "Reset nur ohne aktiven Run"),
    ("const consultOwns", "Consult-Uebernahme ohne emit-Abbruch"),
    ("interimAck", "Zwischenansage-Filter"),
)


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
            raise SystemExit(f"both-sides/{label}: Anker nicht gefunden in {path} — "
                             "Layout hat sich geaendert.")
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    path.with_name(path.name + f".bak-both-sides-{stamp}").write_text(text)
    for _label, anchor, replacement in B_EDITS:
        text = text.replace(anchor, replacement, 1)
    path.write_text(text)
    reread = path.read_text()
    if MARK_B not in reread:
        raise SystemExit(f"both-sides: Verifikation nach dem Schreiben fehlgeschlagen: {path}")
    for needle, what in B_NEEDLES:
        if needle not in reread:
            raise SystemExit(f"both-sides: Teil fehlt ({what}): {path}")
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

    if not result:
        raise SystemExit("Keine passende OpenClaw-Implementierung gefunden.")
    for key, value in sorted(result.items()):
        print(f"{key}: {value}")
    print("Neustart noetig: systemctl --user restart openclaw-gateway.service")
    return 0


if __name__ == "__main__":
    sys.exit(main())
