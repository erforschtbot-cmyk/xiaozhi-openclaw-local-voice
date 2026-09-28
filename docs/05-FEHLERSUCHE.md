# 5. Fehlersuche und bekannte Fehlwege

## Logs

```bash
journalctl --user -u jarvis-realtime-bridge.service -f
journalctl --user -u openclaw-gateway.service -f
```

## Werkzeugfrage bricht ab: „Es hat leider nicht geklappt … soll ich es nochmal?"

Symptom: Die gesprochene Antwort kommt inhaltlich an, aber der Turn gilt als
abgebrochen. Im Gateway-Journal:

```text
lane=talk ... error="SQLite transcript changed while preparing rewrite for <sessionId>"
lane=session:agent:voice:xiaozhi-realtime-v5-... ...
[plugins] OpenAI GPT-Live delegation consult failed: SQLite transcript changed ...
errorName=SqliteTranscriptMutationConflictError
```

**Muster (wichtig):** Tritt fast nur beim **ersten Werkzeug-Consult einer frischen
Voice-Session** auf — also nach einer Pause beziehungsweise nach dem ersten
Verbindungsaufbau. Innerhalb desselben Gesprächs gehen weitere Werkzeugfragen
durch. Eine reine Plauderfrage („erzähle einen Witz") löst es nicht aus, weil sie
keinen Consult braucht — sie etabliert die Session aber, so dass der nächste
Werkzeugaufruf nicht mehr „der erste" ist.

Ursache: Die Zwischenansage des Providers und der Agenten-Consult schreiben in
dasselbe SQLite-Transkript; auf einem frischen Transkript kollidieren beide
Schreiber und der Consult wird vor dem Werkzeugaufruf verworfen.

**Gegenmaßnahme:** Patch B aus `docs/03-OPENCLAW-PATCH.md` anwenden
(Assistant-Transkript nicht persistieren). Danach Gateway neu starten.

Die Session ist dabei **nicht** beschädigt — der Turn wird atomar verworfen.

## Anzeige „Zuhören", aber keine Folgeäußerung wird erkannt

Audio-Turn und Talk-Session dürfen nicht blind gekoppelt werden. Der bestätigte
Stand hält bei einer Werkzeug-Rückfrage dieselbe Talk-Session offen und öffnet nach
physischem `playback_drained` den Eingang erneut. Keine Timer auf Verdacht ändern.

## „Sprechen" oder „Zuhören" bleibt hängen

Seit dem Watchdog-Patch (siehe `docs/02-FIRMWARE.md`) löst die Firmware das selbst:

- kein Serveraudio seit 60 s im Zustand „Sprechen“ → Bereitschaft;
- keine Serveraktivität seit 120 s im Zustand „Zuhören“ → Bereitschaft;
- Audio-Kanal öffnet nicht binnen 15 s → Bereitschaft.

Logbeleg:

```text
W (...) Application: Stuck watchdog: state=speaking in_state=61s idle=60s (...) -> idle
```

Wenn es **trotzdem** hängt:

1. Prüfen, ob die installierte Firmware den Watchdog enthält (die App-Version ist
   dabei nicht aussagekräftig — nur Compile-Zeit und ELF-Hash, siehe
   `scripts/read-device-info.py`).
2. Prüfen, ob der Zustand von einer **nicht** überwachten Phase stammt
   (`wifi_configuring`, `activating`, `upgrading`) — diese schützt der Watchdog
   bewusst nicht.
3. Als Sofortmaßnahme `systemctl --user restart jarvis-realtime-bridge.service`
   — der erzwungene Socket-Abbruch bringt das Gerät nach Idle.

Weitere Punkte, die im Gesprächspfad gelten:

- `stream_end` muss an das Gerät gesendet werden.
- Das Gerät meldet nach geleertem Puffer `playback_drained`.
- WebSocket-Close braucht `close_timeout=1`; das Board liefert nicht immer einen
  Close-Frame.

### Ursache: Zwischenansage und Antwort in einer Nachricht

Symptom: Im **Werkzeugfall** geht „Sprechen“ nicht weg, obwohl Jarvis gesprochen
hat. Im Bridge-Log fehlt `Audio stream complete` (also `stream_end`).

```text
assistant_done  text=" I'll check that request. Es ist 01:02 Uhr."
(kein "Audio stream complete")
```

Ursache: `is_interim_text()` in `gateway/server.py` prüfte nur ein **Präfix**
(`startswith`). Wachsen Zwischenansage und Antwort zu **einer** Nachricht zusammen,
gilt die ganze Zeile als Zwischenansage — `schedule_stream_end()` wird
übersprungen und das Gerät bleibt im Zustand „Sprechen“.

Gegenmaßnahme: `is_interim_text()` vergleicht jetzt gegen eine **geschlossene
Liste** reiner Zwischenansagen (nach Trimmen von Satzzeichen). Eine
zusammengewachsene Nachricht ist damit **nicht** interim und bekommt `stream_end`.

Gegenprobe im Log: Bei getrennten Nachrichten war es unauffällig, weil die
Zwischenansage (`„Einen Moment, ich prüfe das.“`) und die Antwort (`„Es ist 01:02
Uhr.“`) als **zwei** `assistant_done`-Events ankamen.

## Gerät verbindet nach jeder Antwort neu

```text
Follow-up window expired; returning to standby ...
XiaoZhi session failed: sent 1000 (OK) follow-up timeout; no close frame received
XiaoZhi connected from ('...')
```

Das ist **erwartet**: Das Board antwortet nicht immer mit einem Close-Frame; die
Bridge wartet 2 s und bricht dann hart ab (`transport.abort()`). Folge: pro Antwort
entsteht eine neue Voice-Session. Kein Fehler, aber es erklärt, warum so viele
Session-IDs auflaufen.

## Sehr viele `xiaozhi-realtime-v5-…`-Sessions (Ist-Stand 27.09.2026)

Zwei getrennte Ursachen nicht verwechseln:

- **Behoben:** Der Talk- und der Consult-Schlüssel enthielten früher eine UUID
  (`…-{turn_id}` / `…-{session_id}`). Dadurch erzeugte **jeder Werkzeug-Consult**
  eine eigene Session. Seit dem 27.09.2026 sind beide Schlüssel **konstant**
  (siehe `README.md`, „Dauerhafte Talk-Sitzung“); Werkzeugfragen wachsen nicht mehr
  als neue Session-Kennungen auf.
- **Erwartet und weiter vorhanden:** Nach jeder Antwort bricht die Bridge die
  WebSocket-Verbindung ab und das Gerät verbindet neu (siehe oben). Pro Antwort
  entsteht dadurch weiterhin eine neue Session-ID. Das ist kein Fehler.

Nebeneffekt der konstanten Schlüssel: Werkzeug-Antworten sind deutlich schneller,
weil die Sitzung warm bleibt und der Prompt-Cache greift.

## Werkzeug-Antwort plötzlich viel schneller (Ist-Stand 27.09.2026)

Kein Zufall: `gateway/server.py` verwendet **konstante** Talk-/Consult-Schlüssel.
Statt pro Frage eine neue Session aufzubauen, läuft alles in einer warmen Session;
im Tool-Turn ist `cacheRead` gesetzt. Belege und Rückweg stehen in `README.md`.

## Antwortanfang oder -ende abgeschnitten

Keine pauschalen Startpuffer oder langen Audio-Silence-Timer ergänzen. Frühere
Versuche mit 180 ms Startpuffer beziehungsweise 1,2 s Audio-Pause beschädigten den
funktionierenden Ablauf. Änderungen immer einzeln testen und rückrollbar halten.

## Firmware-Build verliert Mikrofon/Audio

Zuerst das verifizierte App-Image bei `0x20000` wiederherstellen. Nicht ungeprüft
das gesamte Flashlayout ersetzen. Assets bei `0x800000` und NVS erhalten.

## Nicht sicher, welche Firmware läuft?

Rein lesend prüfen, ohne das Gerät zu verändern:

```bash
# Boot-Log: Project, Version, Compile time, ELF-SHA256
sudo python3 scripts/read-device-info.py   # oder seriell mitlesen

# App-Deskriptor direkt lesen (nur read, kein write/erase)
esptool --port /dev/ttyACM0 --no-stub read-flash 0x20020 0x100 /tmp/desc.bin
```

Der Deskriptor (`esp_app_desc_t`) beginnt direkt am gelesenen Offset `0x20020`:

| Feld | Offset | Länge |
|---|---:|---:|
| magic (`0xabcd5432`) | `0x00` | 4 |
| version | `0x10` | 32 |
| project | `0x30` | 32 |
| compile time | `0x50` | 16 |
| compile date | `0x60` | 16 |
| ESP-IDF | `0x70` | 32 |
| ELF-SHA256 | `0x90` | 32 |

**Verwechslungsfalle:** Es existieren mehrere `xiaozhi 2.5.0`-Builds. Unterscheiden
lässt sich nur über die **Compile time** und den **ELF-Hash**, nicht über Version
oder Projektname.

## OpenClaw-Update

Nach Updates sind die `dist`-Patches überschrieben. Patch, Verifikation,
Gateway-Neustart und End-to-End-Werkzeugtest sind Pflicht:

```bash
./scripts/apply-openclaw-voice-dist-patches.py
./scripts/verify-openclaw-voice-dist-patches.py
systemctl --user restart openclaw-gateway.service
```
