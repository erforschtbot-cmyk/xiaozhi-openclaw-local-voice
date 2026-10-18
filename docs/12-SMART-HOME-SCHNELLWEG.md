# 12. Schnellweg für einfache Schaltbefehle (Gateway-Weiche)

Stand 2026-10-18, auf dem Referenzhost **belegt und im Betrieb**.

Ein eindeutiger Wohnungsbefehl wie „Schalte den Monitor aus“ wird **ohne
KI-Lauf** direkt an Alexa übergeben. Alles andere — mehrere Aktionen,
Verzögerungen, Bedingungen, Fragen, unbekannte Namen — geht **vollständig**
an den Agenten. Es gibt keinen Zwischenzustand: entweder direkter Aufruf oder
KI, niemals beides.

**Belegter Lauf (Referenzhost):**

```text
[jarvis-fast-route] accepted device="Monitor" action=aus http=200 elapsedMs=27
```

Mehrere Messungen im selben Betrieb: **7 ms, 14 ms, 16 ms, 27 ms, 28 ms, 51 ms**
vom erkannten Satz bis zur Annahme durch Alexa. Keine Tokens, kein Agentenlauf.

---

## Warum das nicht einfach „Schlüsselwörter matchen“ ist

Ein naiver Filter („enthält *Monitor* und *aus*“) schaltet bei
„Schalte den Monitor aus **und danach** den Deckenventilator an“ bereits den
Monitor — der Agent schaltet danach den Ventilator. Genau das darf nicht
passieren.

Die Weiche prüft deshalb den **vollständigen Satz** und entscheidet vor dem
Agentenlauf:

| Bedingung | Erfüllt heißt |
|---|---|
| Quelle ist die native Sprachdelegation | nicht Tool-Call, nicht Fremdkanal |
| Session ist eine Jarvis-Voice-Session | `agent:voice:xiaozhi-realtime-v5` **und** `…-v6` (Muster `^agent:voice:xiaozhi-realtime-v\d+$`) |
| `input` ist **zeichengleich** mit dem kompletten Nutzersatz aus `transcript_delta` | keine vom Modell gekürzte oder umformulierte Anfrage |
| Satz matcht als **Ganzes** genau ein bekanntes Gerät + eine An/Aus-Aktion | keine Teilstrings |
| Gerätename im Katalog **eindeutig** und Katalog ≤ 48 h alt | sonst Agent |

Fällt eine Bedingung aus, läuft der **unveränderte** Agentenpfad. Die Weiche
greift also nur, wenn sie den ganzen Satz sicher versteht.

## Was bewusst NICHT direkt läuft

- Mehrere Aktionen, `und`, `oder`, `danach`, `dann`, `vorher`, `gleichzeitig`
- Verzögerungen, Zeitangaben (`in 10 Sekunden`, `morgen`), Bedingungen (`wenn`, `falls`)
- Verneinungen (`nicht`), Fragen (`Ist der Monitor aus?`), Zusatztext
- Unbekannte, doppelte oder mehrdeutige Gerätenamen
- Dimmen, Farben, Jalousie-Positionen, Szenen — dafür fehlt die eindeutige Wertzuordnung
- Alarm, Schlösser, Wasser, Heizung, Herd, Auto/Rechner, beliebige virtuelle Schalter

Diese Fälle können nur mit Kontext entschieden werden — also Agent.

## Geräteliste kommt aus ioBroker (Alexa2-Adapter)

`gateway/refresh-alexa-catalog.mjs` liest **nur** die Alexa2-Objekte:

- Gerätename: `native.friendlyName`
- Schaltfähigkeit: vorhandenes, schreibbares `.powerState`
- Ergebnis: `alexa-device-catalog.json` im Bridge-Verzeichnis

Der Katalog wird **atomar** ersetzt und nur bei vollständigem Erfolg
geschrieben. Fehlt er, ist er beschädigt oder älter als 48 Stunden, ist die
Weiche automatisch **aus** (fail-closed) — die KI bleibt verfügbar.

Erneuerung täglich über `systemd/jarvis-alexa-catalog.timer`; zusätzlich von
Hand:

```bash
node ~/.local/share/jarvis-realtime-bridge/refresh-alexa-catalog.mjs
```

Auf dem Referenzhost ergab der erste Lauf: **102 erkannte Alexa-Einträge,
23 zugelassene Schaltgeräte** — unter anderem `Monitor`, `Monitorlicht`,
`Lampe Schlafzimmer`, `Fernseher`, `Steckdose Sofa`, `Ventilator Arbeitszimmer`.

> **Namensfalle:** In der Alexa-Liste heißt der Ventilator
> `Ventilator Arbeitszimmer`, **nicht** „Deckenventilator“. Eine Äquivalenz
> wird nicht geraten; „Deckenventilator an“ geht deshalb an den Agenten.
> Generisches „Licht an“ braucht ebenfalls ein eindeutig bekanntes Ziel —
> es gibt kein angenommenes Standardzimmer.

## Zwei Teile im Gateway (`dist`-Patch)

Beides liegt in kompilierten Dateien und wird von jedem OpenClaw-Update
überschrieben.

| Marker | Datei | Zweck |
|---|---|---|
| `jarvis-safe-router-v1` | `dist/handlers-*.mjs` | Weiche am Consult-Eingang, ruft `routeSmartHome` |
| `jarvis-direct-completion-v2` | `dist/handlers-*.mjs` | schließt den Turn **ohne** Agentenlauf korrekt ab |

**Warum W2 nötig ist:** Auf dem direkten Weg startet bewusst kein Agent. Der
normale Abschluss wartet aber auf einen KI-Lauf. Ohne W2 wird die Antwort
verworfen, das Gerät bekommt kein `stream_end` und bleibt im Zustand
„Sprechen“ (hellblau) stehen — **belegt** am 2026-10-18. W2 bindet den
Abschluss stattdessen an den bestehenden Voice-Owner und verifiziert dabei
weiter Session, Abbruch und Lifecycle-Generation.

Anwenden und prüfen:

```bash
./scripts/apply-jarvis-smart-home-router.py
./scripts/verify-jarvis-smart-home-router.py
systemctl --user restart openclaw-gateway.service
```

Das Skript ist idempotent (vorhandene Marker werden nicht erneut geschrieben)
und legt vor jeder Änderung `<datei>.bak-smart-home-router-<zeitstempel>` an.
Es bricht mit klarer Meldung ab, wenn das OpenClaw-Layout sich geändert hat —
dann muss der Anker angepasst werden, statt blind zu schreiben.

## Ein Ausführer, nicht zwei

Der alte Bridge-Regex-Direktpfad bleibt **aus**:
`~/.local/share/jarvis-realtime-bridge/disable-smart-home-fastpath` muss
existieren. Sonst könnten zwei substring-basierte Pfade denselben Satz
schalten und „Monitor aus und danach …“ vorzeitig auslösen.

Innerhalb des Gateways gilt zusätzlich:

- Ein Treffer reserviert **vor** dem Schreibzugriff; gleichzeitige identische
  Anfragen im selben Voice-Turn teilen sich **ein** Ergebnis (auch Fehler).
- Nach einem Schreibversuch gibt es **keinen** Blind-Retry und **kein**
  Durchfallen zum Agenten — sonst Doppelschalten.
- Timeout oder HTTP-Fehler liefern eine ehrliche Unsicherheit, keinen Erfolg.

**HTTP 200 heißt „von Alexa angenommen“, nicht „Gerät hat geschaltet“.**
Für einen Gerätebeweis ist ein Read-back am Ziel-Datenpunkt nötig.

## Rückweg bei hängendem Gerät

Zwei Schichten, unabhängig voneinander:

1. **Bridge-Wächter** (`server.py`): Bleibt eine Runde ohne `stream_end`
   länger als 45 s ohne Audio stehen, sendet die Bridge `tts stop` und trennt
   die Verbindung. Dadurch geht das Gerät in Bereitschaft, ohne dass der
   Nutzer etwas sagen muss.
2. **Geräte-Watchdog** (Firmware, siehe `docs/09-FIRMWARE-WACHWORT.md`).

Zusätzlich wird der Wächter bei jeder neuen Äußerung neu bewaffnet, auch wenn
vor dem finalen Transkript bereits Provider-Audio eintraf.

## Prüfung ohne Gerätezugriff

```bash
node --test ~/.local/share/jarvis-realtime-bridge/test-smart-home-router.mjs
```

**41 Tests**, alle Schreibvorgänge sind Attrappen — es wird kein Gerät
geschaltet. Abgedeckt: beide Session-Versionen (v5/v6/v7) und fremde
Session-Keys, dein Beispielsatz mit Verzögerung, Fragen, Verneinungen,
Zeitangaben, doppelte und unbekannte Namen, abgelaufener/fehlender Katalog,
gleichzeitige Doppelaufrufe, Timeout/HTTP-Fehler ohne Retry, Abbruch.

## Nach jedem OpenClaw-Update

1. `./scripts/verify-jarvis-smart-home-router.py` — meldet fehlende Marker
   oder rote Tests.
2. Falls fehlend: `./scripts/apply-jarvis-smart-home-router.py`.
3. Gateway durch den Owner neu starten lassen.
4. Ein echtes „Monitor aus“ sprechen und im Log **genau eine** Zeile
   `[jarvis-fast-route] accepted …` prüfen.

Ein mehrteiliger Satz darf **keine** solche Zeile erzeugen und muss normal
vom Agenten beantwortet werden.
