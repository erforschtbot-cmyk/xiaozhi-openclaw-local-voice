# 3. OpenClaw: Voice-Patches in `dist`

OpenClaw wird für den Voice-Pfad an **vier** Stellen verändert. Alle liegen in
**kompilierten `dist`-Dateien** und werden von **jedem OpenClaw-Update
überschrieben**. Nach jedem Update erneut anwenden und verifizieren.

Anwenden/Prüfen übernimmt das Repo-Skript (idempotent, legt vor jeder Änderung
eine Sicherung `<datei>.bak-<marker>-<zeitstempel>` an):

```bash
./scripts/apply-openclaw-voice-dist-patches.py
./scripts/verify-openclaw-voice-dist-patches.py
systemctl --user restart openclaw-gateway.service
```

---

## Patch A — zusätzliche Talk-Sprachbestätigung deaktiviert

- Datei: `dist/agent-tools.before-tool-call-*.mjs`
- Funktion: `resolveClientVoiceToolConfirmationPolicy`
- Marker: `voice-confirmation-disabled-by-owner-v1`

OpenClaw würde verändernde Voice-Werkzeugaktionen zusätzlich per gesprochenem
Ja/Nein absichern. Der Owner hat das ausdrücklich abgeschaltet. Der Patch setzt die
Funktion früh auf `{ allowed: true }` — **ohne** Text-, Befehls-, Satzzeichen- oder
Wortfilter.

---

## Patch B — Frage und Antwort beide persistieren

- Datei: `dist/handlers-*.mjs`
- Ort: `onTranscript` im Realtime-Relay (+ Relay-Feld, provider-direct-Haken)
- Marker: `voice-no-tool-both-sides-v1`

Ziel: Im **Ohne-Tool-Fall** stehen **beide** Texte in der Session — die gesprochene
Frage **und** die KI-Antwort. Im **Mit-Tool-Fall** schreibt der Consult die Antwort;
der Relay schweigt dann, damit nichts doppelt entsteht. Verworfen wird nur die
Zwischenansage („I'll check that request." / „Ich prüfe das kurz.").

Umsetzung an drei Stellen:

| Teil | Was |
|------|-----|
| B1 | Relay-Feld `assistantOwnedByConsult` einführen |
| B2 | Merker im provider-direct-Haken (`handleDelegationInput`) setzen |
| B3 | Transkript-Gate: Zwischenansage verwerfen; Assistant überspringen, wenn ein Consult den Turn besitzt (Merker danach verbrauchen); Reset bei neuer Frage nur ohne aktiven Run |

**Wichtig — kein `return` vor `emit()`:** Das Gate darf ausschließlich das
*Persistieren* auslassen. Ein früher `return` ließ das Gerät im Zustand „spricht"
hängen und schnitt den Ton ab. Das ist die Regel, die diesen Patch von früheren,
verworfenen Versuchen unterscheidet.

> Hinweis: Eine ältere Fassung dieses Dokuments beschrieb unter diesem Punkt etwas
> anderes („Assistant-Transkript wird nicht persistiert", Marker
> `voice-test-suppress-assistant-persist-v1`). Dieser Weg wurde verworfen:
> Unterdrückt man die Assistant-Seite ganz, fehlt im Ohne-Tool-Fall die Antwort.
> Gültig ist allein der obige Stand (`voice-no-tool-both-sides-v1`).

---

## Patch C — gesprochener Satz bleibt sichtbar

- Datei: `dist/builtin-openclaw-*.mjs`
- Funktion: `resolveOrphanRepairPlan`
- Marker: `keep-spoken-test`

Die eigene gesprochene Äußerung kommt als `provenance.kind = "realtime_voice"`.
Ohne Ausstieg hängt die Orphan-Repair den Leaf auf den letzten Assistant zurück;
der gesprochene Satz bleibt dann auf einem Seitenast und verschwindet aus der
sichtbaren Instanz.

Der Patch steigt bei dieser Herkunft früh aus:

```javascript
const keepSpokenProvenance = candidate.messageEntry.message.provenance;
if (keepSpokenProvenance && keepSpokenProvenance.kind === "realtime_voice") return;
```

---

## Patch D — Apostroph in der Zwischenansage auf ASCII

- Dateien: `dist/extensions/openai/capability-catalog.js`,
  `dist/realtime-quicksilver-delegation-controller-*.mjs`
- Marker: `"I'll check that request."`

Setzt das typografische Apostroph auf ASCII: `I’ll` → `I'll`. Rein kosmetisch —
**kein** Sprachwechsel.

> Der frühere Umbau „Englisch → Deutsch" (`I'll check that request.` →
> `Ich prüfe das kurz.`) ist **nicht** mehr nötig und **nicht** Teil der Patches.
> Die Zwischenansage erscheint im aktuellen OpenClaw ohnehin nicht mehr.

---

## Anwenden und verifizieren

```bash
./scripts/apply-openclaw-voice-dist-patches.py
./scripts/verify-openclaw-voice-dist-patches.py
systemctl --user restart openclaw-gateway.service
```

Das Anwenden ist **idempotent**: Ist ein Patch schon aktiv, meldet das Skript
`already` und schreibt nichts.

Erwartete Ausgabe bei aktivem Stand:

```text
apostrophe:capability-catalog.js: already
apostrophe:realtime-quicksilver-delegation-controller-<hash>.mjs: already
confirm:agent-tools.before-tool-call-<hash>.mjs: already
keep-spoken:builtin-openclaw-<hash>.mjs: already
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
