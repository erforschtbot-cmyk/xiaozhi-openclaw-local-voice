# 3. OpenClaw: Voice-Patches in `dist`

OpenClaw verändert für den Voice-Pfad zwei Verhaltensweisen. Beide liegen in
**kompilierten `dist`-Dateien** und werden von **jedem OpenClaw-Update
überschrieben**. Nach jedem Update erneut anwenden und verifizieren.

## Patch A — zusätzliche Talk-Sprachbestätigung deaktiviert

Datei: `dist/agent-tools.before-tool-call-*.mjs`
Funktion: `resolveClientVoiceToolConfirmationPolicy`
Marker: `voice-confirmation-disabled-by-owner-v1`

OpenClaw würde verändernde Voice-Werkzeugaktionen zusätzlich per gesprochenem
Ja/Nein absichern. Der Owner hat das ausdrücklich abgeschaltet. Der Patch setzt die
Funktion früh auf `{ allowed: true }` — **ohne** Text-, Befehls-, Satzzeichen- oder
Wortfilter.

## Patch B — Assistant-Transkript wird nicht persistiert

Datei: `dist/handlers-*.mjs`
Ort: `onTranscript` im Realtime-Relay
Marker: `voice-test-suppress-assistant-persist-v1`

Das Provider-/Assistant-Transkript (gesprochene KI-Antwort und die Zwischenansage)
wird **nicht** in die Voice-Session geschrieben. Das User-Transkript bleibt erhalten.

Grund: Zwischenansage und Agenten-Consult schreiben sonst in dasselbe SQLite-Transkript.
Bei einem frischen Turn (erster Werkzeug-Consult nach einer Pause) kollidieren beide
Schreiber und der Turn wird verworfen:

```text
SqliteTranscriptMutationConflictError:
SQLite transcript changed while preparing rewrite for <sessionId>
```

Sichtbare Folge ohne Patch: „Es hat leider nicht geklappt … soll ich es nochmal?" —
obwohl die Werkzeugantwort inhaltlich bereits erzeugt wurde.

## Anwenden und verifizieren

```bash
./scripts/apply-openclaw-voice-dist-patches.py
./scripts/verify-openclaw-voice-dist-patches.py
systemctl --user restart openclaw-gateway.service
```

Das Anwenden ist **idempotent**: Ist ein Patch schon aktiv, meldet das Skript
`already` und schreibt nichts. Vor jeder Änderung wird eine Sicherung
`<datei>.bak-<marker>-<zeitstempel>` angelegt.

Erwartete Ausgabe bei aktivem Stand:

```text
confirm:agent-tools.before-tool-call-<hash>.mjs: already
transcript:handlers-<hash>.mjs: already
Neustart noetig: systemctl --user restart openclaw-gateway.service
```

## Nach jedem OpenClaw-Update

```bash
./scripts/apply-openclaw-voice-dist-patches.py
./scripts/verify-openclaw-voice-dist-patches.py
systemctl --user restart openclaw-gateway.service
```

Danach muss der Werkzeugtest aus `04-TESTPLAN.md` real ausgeführt werden. Eine
erfolgreiche Syntax-/Markerprüfung allein beweist die Funktion nicht, und ein
`already` ohne Neustart bedeutet: der laufende Gateway hat noch den alten Code.

## Wenn sich das Bundle-Layout ändert

Die Skripte suchen ihre Ankerzeilen wörtlich. Findet ein Skript seinen Anker nicht,
bricht es mit klarer Meldung ab und schreibt nichts:

```text
<label>: Anker nicht gefunden in <pfad> — Layout hat sich geaendert.
```

Dann Ankerzeile im neuen `dist` suchen, Skriptkonstante anpassen, erneut prüfen.
Niemals blind patchen.
