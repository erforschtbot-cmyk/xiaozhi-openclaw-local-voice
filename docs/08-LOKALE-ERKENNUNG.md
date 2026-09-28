# Lokale Spracherkennung (faster-whisper)

Dieses Kapitel erklärt, wie das gesprochene Wort des Nutzers ohne Cloud-Dienst
zu Text wird. Nach diesem Schritt verlässt **kein Mikrofon-Audio** mehr den
Host.

## Was sich ändert

```text
VORHER (Erkennung beim Provider)
  Gerät ──Mikrofon──► Bridge ──► OpenAI ──► Text ──► Agent
                                  (hört UND führt Gespräch)

NACHHER (Erkennung lokal)
  Gerät ──Mikrofon──► Bridge ──► lokaler Whisper ──► Text ──► Agent
                                  (nur Erkennung)
```

Mit lokaler Erkennung fällt der OpenAI-Realtime-Dienst **vollständig** weg.
Er machte vorher drei Dinge:

| Aufgabe | vorher | nachher |
|---|---|---|
| Sprache erkennen | OpenAI | **lokaler Whisper** |
| Gespräch führen | OpenAI | **OpenClaw-Agent direkt** |
| Agent bei Werkzeugbedarf aufrufen | OpenAI | entfällt (Agent hat die Werkzeuge) |

Deshalb ist der lokale Helfer (`gateway/local-voice.mjs`) kein umgebauter
Provider-Helfer, sondern ein **eigener, kleinerer** Weg: Audio rein, Text
raus, Agent aufrufen, Antworttext melden.

## Warum das schnell sein muss

Erkennung passiert **nach** dem Sprechen und **vor** der Antwort. Jede Sekunde
hier ist eine Sekunde Stille für den Nutzer. Gemessen auf dem Referenzhost
(Intel i5-8365U, CPU, int8) für einen 3,12-s-Satz:

| Modell | Zeit | RTF | Urteil |
|---|---|---|---|
| `base` | 0,81 s | 0,26 | schnell, verhört sich gelegentlich |
| `small` | 2,18 s | 0,70 | **Standard** — genauer |
| `medium` | 7,3–15,0 s | 2,3–4,8 | **untauglich** |

Der früher laufende `medium`-Dienst war 9- bis 18-mal langsamer als `base`.
Auf diesem Host sind `base` und `small` tragfähig; gewählt wurde `small`,
weil die Erkennungsfehler von `base` im Sprachbetrieb störten
(„Schalte“ → „Zeite“).

Beobachtete Genauigkeit an echtem Mikrofon-Audio:
- `base`: „Schalte den Monitor an. Wie spät ist es?“ → „Zeite den Monitor
  an, wie spät es ist.“
- `small`: derselbe Satz → „Wie spät ist das?“ (Wörter und Satzbau besser,
  einzelne Verwechslungen bleiben)

Beide Modelle brauchen Vorlaufzeit beim ersten Satz, wenn der Dienst sie noch
nicht geladen hat. Modelle vorab laden, siehe Einrichtung unten.

## Einrichten

### 1. Whisper-Dienst betreiben

Der Dienst läuft getrennt von der Bridge (systemd-Userdienst):

```ini
# ~/.config/systemd/user/wyoming-stt.service
[Service]
ExecStart=%h/wyoming-stt/.venv/bin/wyoming-faster-whisper \
  --uri tcp://0.0.0.0:10300 \
  --data-dir %h/.cache/faster-whisper-models \
  --download-dir %h/.cache/faster-whisper-models \
  --model small \
  --compute-type int8 \
  --language de \
  --beam-size 5
```

Wichtig: `--model small` (oder `base`). Der Standard vieler Anleitungen ist
`medium` — das ist auf CPU-Hardware ohne Grafikkarte zu langsam.

Modell vorladen (sonst lädt der Dienst es beim ersten Satz nach):

```bash
python3 -c "from faster_whisper import WhisperModel; \
  WhisperModel('small', device='cpu', compute_type='int8', \
  download_root=os.path.expanduser('~/.cache/faster-whisper-models'))"
```

### 2. Bridge umstellen

```bash
scripts/install-host.sh --public-host 192.168.178.143
# Standard: lokale Erkennung UND lokale Sprachausgabe

scripts/install-host.sh --public-host 192.168.178.143 \
  --stt-uri 127.0.0.1:10300
# andere Adresse fuer den Whisper-Dienst
```

Die lokale Erkennung setzt die lokale Sprachausgabe voraus und schaltet sie
mit ein: Der lokale Helfer liefert kein Audio; ohne Piper wäre nichts zu
hören.

### 3. Prüfen

```bash
scripts/verify-host.sh                 # Dienst, Modell, Ports
node tests/test_local_stt.mjs          # Erkennung + Aeusserungsgrenzen
node tests/test_local_helper_e2e.mjs   # Audio -> Text -> Agent -> Antwort
```

## Wie Aeusserungsgrenzen erkannt werden

Vorher entschied OpenAIs VAD, wann ein Satz zu Ende ist. Jetzt macht das die
Bridge selbst (`UtteranceDetector` in `gateway/local_stt.mjs`):

- Der **Effektivwert (RMS)** je 60-ms-Rahmen entscheidet, ob gerade gesprochen
  wird.
- Das **Grundrauschen** wird laufend gelernt, solange niemand spricht. Der
  Schwellwert liegt darüber, damit leises Brummen nicht als Sprache gilt.
- Bleibt es **700 ms** still, gilt die Äußerung als beendet und wird an
  Whisper geschickt.

Der frühere Provider wartete **3000 ms** Stille. Diese 2,3 Sekunden fallen
hier weg — das ist der größte Latenzgewinn des Umbaus.

## Antworttext und Sprachausgabe

Der Agent antwortet häufig **formatiert** („Es ist \*\*13:30 Uhr\*\*",
Aufzählungen, Links). Eine Sprachausgabe darf davon nichts aussprechen. Vor
der Synthese entfernt `strip_markdown()` in `gateway/piper_speaker.py` die
Formatierung, behält aber den Inhalt.

Nachgewiesen: Derselbe Satz mit und ohne Sternchen ergibt unterschiedlich
langes Audio — Piper spricht `**` tatsächlich mit (2,74 s statt 2,68 s).

## Vier Fehler, die erst im echten Betrieb auftraten

Diese sind alle behoben. Sie stehen hier, weil keiner davon durch Tests mit
künstlichem Audio aufgefallen wäre — der wichtigste Hinweis für Nachbauer.

1. **Sprachschwelle an falschem Material kalibriert.** Ein Test mit
   Piper-Audio (RMS ~1755) setzte die Schwelle auf 420. Ein echtes Mikrofon
   liefert für Sprache nur RMS 40–110 bei Stille 14–20. Folge: Sprache wurde
   fast nie erkannt, Äußerungen still verworfen — das Gerät blieb im
   Zustand „hört zu", ohne dass etwas geschah. Richtig ist
   `speechRms: 35` plus Grundrauschen-Adaption.
2. **Falsches Feld für den Antworttext.** Der Agent liefert Text in
   `payload.message.content` (Liste von Teilen), nicht in `payload.text`.
   Ein `payload.text`-Zugriff ergibt immer leer — die Stimme verstummt still.
   Beide Formen abdecken (siehe `extractText` in `gateway/local-voice.mjs`).
3. **Das Wachwort wurde als Auftrag verschickt.** Da das Gerät Mikrofon
   fortlaufend sendet, wird auch das gesprochene „Jarvis" transkribiert.
   Ohne Filter geht es als Frage an den Agenten. `isWakeWordOnly()` verwirft
   reine Wachworte.
4. **Zwei Äußerungen liefen gleichzeitig.** Wurde `busy` erst nach der
   Transkription gesetzt, konnte eine zweite Äußerung die laufende Antwort
   überschreiben; die erste ging verloren. `busy` wird jetzt **sofort**
   gesetzt, die nächste Äußerung vorgemerkt und danach verarbeitet.

Lehre: Die Sprachschwelle immer an **echtem Mikrofon-Audio** kalibrieren,
nie an Synthese-Ausgabe.

## Zurück zur Provider-Erkennung

```bash
scripts/install-host.sh --public-host 192.168.178.143 --provider-stt
```

Damit läuft wieder der OpenAI-Realtime-Weg samt dessen VAD. Es muss nichts
umgebaut werden; beide Pfade sind im Code erhalten.

## Bekannte Grenzen

- **Das Denken bleibt beim Agenten.** Der Agent läuft weiterhin über ein
  Modell, das je nach Einrichtung lokal oder in der Cloud liegt. Dieser
  Aufbau macht Erkennung und Stimme lokal, nicht das Modell.
- **`small` verhört sich gelegentlich.** Beispiele: „Schalte" → „Zeite".
  Bei Werkzeugbefehlen kann das Folgen haben. `base` ist schneller, aber
  ungenauer; die Wahl ist ein Abwägen zwischen Wartezeit und Treffsicherheit.
- **Die Stille-Erkennung ist schwellwertbasiert.** In sehr lauter Umgebung
  kann sie eine Äußerung zu früh oder zu spät beenden. Die Schwellen sind
  über Umgebungsvariablen einstellbar (`OPENCLAW_VAD_RMS`,
  `OPENCLAW_VAD_SILENCE_MS`, `OPENCLAW_VAD_MIN_SPEECH_MS`).
- **Kein Zwischenergebnis.** Es gibt keine Vorschau während des Sprechens,
  weil Whisper ganze Äußerungen verarbeitet. Der Text erscheint erst nach
  der Sprechpause.
- **Erkennung braucht den Dienst.** Läuft `wyoming-stt` nicht, schlägt jede
  Äußerung fehl; die Bridge meldet dann einen Fehler und spricht nichts.
