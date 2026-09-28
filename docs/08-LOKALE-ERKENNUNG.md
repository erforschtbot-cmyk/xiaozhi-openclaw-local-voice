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

## Zerlegung steuern (`--tts-split`)

Wie kleinteilig gesprochen wird, ist einstellbar:

| Modus | Trennt bei | Wirkung |
|---|---|---|
| `clause` | Komma und Satzzeichen | frühester Beginn, klingt zerstückelter |
| `sentence` | Satzzeichen (Standard) | guter Kompromiss |
| `whole` | gar nicht | die ganze Antwort in einem Stück: spätester Beginn, ruhigster Klang |

```bash
--tts-split clause     # bis zum Komma senden
--tts-split sentence   # bis zum Punkt senden (Standard)
--tts-split whole      # alles auf einmal
```

Der Zusammenhang: Früher trennen heißt **früher anfangen zu sprechen**, aber
mehr Nahtstellen zwischen den Stücken. `whole` klingt am ruhigsten, lässt den
Nutzer aber warten, bis die ganze Antwort fertig ist.

**Wichtig:** Ein Schnitt mitten in einer Zahl klingt hörbar abgehackt. Der
Code schützt deshalb Ziffer-zu-Ziffer-Zeichen: Uhrzeiten (`13:52`) und
Dezimalzahlen (`13.95`) werden nie getrennt. Ohne diesen Schutz ergäbe
„Es ist 13:52 Uhr" ein „Es ist 13:" — genau der abgehackte Eindruck.

## Stimm-Feinregler

Piper kennt vier Regler, die jetzt durchgereicht werden (weggelassen =
Pipers Standard):

| Schalter | Wirkung |
|---|---|
| `--tts-length-scale` | Tempo: 1.0 normal, kleiner = schneller, größer = langsamer |
| `--tts-noise-scale` | Aussprache-Variation: kleiner = gleichmäßiger |
| `--tts-noise-w-scale` | Lautlängen-Variation: kleiner = gleichmäßiger |
| `--tts-volume` | Lautstärke der Synthese (nicht die Gerätelautstärke) |

Gemessen an „Es ist 13:52 Uhr." (Kerstin):

| Einstellung | Audiolänge |
|---|---|
| Standard | 2,56 s |
| `--tts-length-scale 0.9` | 2,42 s (schneller) |
| `--tts-length-scale 1.15` | 2,82 s (langsamer) |

Diese Regler ändern den **Klang**, nicht die Taktung zum Gerät. Die
60-ms-Rahmen bleiben unverändert.

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
3. **Das Wachwort wurde als Auftrag verschickt.** Das Gerät öffnet den
   Audiokanal erst, wenn es das Wachwort gehört hat; je nach Zeitpunkt
   liegen die ersten Rahmen noch davor und enthalten es. Whisper schreibt
   „Jarvis" sehr unterschiedlich — im Betrieb beobachtet: „Jarvis.",
   „Ja, das...", „Job es.", „Ja, bis da an den Witz.". `cleanTranscript()`
   schneidet führende Wachwort-Reste ab und verwirft reine Wachworte; kurze
   Fehldeutungen (≤ 3 Wörter) werden ebenfalls verworfen, damit „Ja, das ist
   gut." als echter Satz erhalten bleibt.
4. **Zwei Äußerungen liefen gleichzeitig.** Wurde `busy` erst nach der
   Transkription gesetzt, konnte eine zweite Äußerung die laufende Antwort
   überschreiben; die erste ging verloren. `busy` wird jetzt **sofort**
   gesetzt, die nächste Äußerung vorgemerkt und danach verarbeitet.
5. **Doppelpunkt in der Uhrzeit galt als Satzgrenze.** `"Es ist 13:52 Uhr"`
   wurde zu `"Es ist 13:"` zerschnitten — hörbar abgehackt mitten in der
   Zahl. Behoben: Steht links und rechts des Zeichens eine Ziffer, ist es
   keine Sprechgrenze.

Lehre: Die Sprachschwelle immer an **echtem Mikrofon-Audio** kalibrieren,
nie an Synthese-Ausgabe.

## Wachwort-Reste im Transkript

Das Gerät öffnet den Audiokanal erst, wenn es das Wachwort gehört hat. Je
nach Zeitpunkt liegen die ersten Audiorahmen noch **davor** — das Wachwort
steckt dann mit im Transkript. Die Ursache liegt damit im Gerät (Firmware),
die Bereinigung gelingt aber auf dem Host und ist der sicherere Weg: Ein
Firmware-Neubau kann Mikrofon/Audio verlieren (siehe
`docs/06-BUILD-VERIFICATION.md`).

Whisper schreibt „Jarvis" sehr unterschiedlich. Im echten Betrieb beobachtet:

| Transkript | Bewertung |
|---|---|
| `Jarvis.` | reines Wachwort → verwerfen |
| `Ja, das...` | Fehldeutung → verwerfen |
| `Job es.` | Fehldeutung → verwerfen |
| `Ja, bis da an den Witz.` | Wachwort-Rest + Auftrag |
| `Jarvis, erzähle einen Witz.` | Wachwort-Rest + Auftrag |

`cleanTranscript()` in `gateway/local_stt.mjs` behandelt beide Formen:

1. **Führende Wachwort-Reste abschneiden** — der Auftrag dahinter bleibt:
   `"Jarvis, erzähle einen Witz."` → `"erzähle einen Witz."`
2. **Reine Wachwort-Reste verwerfen** — es gibt keinen Auftrag.
3. **Kurze Fehldeutungen verwerfen** — aber nur bei höchstens drei Wörtern,
   damit ein echter Satz wie `"Ja, das ist gut."` nicht verschluckt wird.

Geprüft mit `tests/test_wake_word_clean.mjs`; die Fälle stammen ausnahmslos
aus echten Bridge-Logs.

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
  **Fuer den Alltagsbetrieb empfehlen wir stattdessen Canary** — siehe
  [`10-CANARY-FAVORIT.md`](10-CANARY-FAVORIT.md): es versteht kurze deutsche
  Befehle am Gerät besser und ist vergleichbar schnell.
- **Die Stille-Erkennung ist schwellwertbasiert.** In sehr lauter Umgebung
  kann sie eine Äußerung zu früh oder zu spät beenden. Die Schwellen sind
  über Umgebungsvariablen einstellbar (`OPENCLAW_VAD_RMS`,
  `OPENCLAW_VAD_SILENCE_MS`, `OPENCLAW_VAD_MIN_SPEECH_MS`).
- **Kein Zwischenergebnis.** Es gibt keine Vorschau während des Sprechens,
  weil Whisper ganze Äußerungen verarbeitet. Der Text erscheint erst nach
  der Sprechpause.
- **Erkennung braucht den Dienst.** Läuft `wyoming-stt` nicht, schlägt jede
  Äußerung fehl; die Bridge meldet dann einen Fehler und spricht nichts.

## Zwei Erkennungsdienste betreiben

Die Bridge ist nicht auf Whisper festgelegt. Jeder Dienst, der das
Wyoming-ASR-Protokoll spricht, passt hinter `--stt-uri`. Damit lässt sich
Whisper (Rueckfall) und der Favorit **parallel** betreiben und mit
`scripts/compare-stt.sh` direkt vergleichen:

| Dienst | Port | Rolle |
|---|---|---|
| Canary 180M Flash INT8 + Silero-VAD | `10301` | **Favorit** |
| faster-whisper small + Silero-VAD | `10300` | Rueckfall |

Einzelheiten, Installation und Umschalten: [`10-CANARY-FAVORIT.md`](10-CANARY-FAVORIT.md).
