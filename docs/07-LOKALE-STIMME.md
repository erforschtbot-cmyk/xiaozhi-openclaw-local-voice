# Lokale Sprachausgabe (Piper)

Dieses Kapitel erklärt, wie die Sprachausgabe des XiaoZhi-Geräts lokal auf dem
Host entsteht — ohne Cloud-Stimme. Es beschreibt, was sich gegenüber dem
Provider-Audio ändert, wie man die Stimme wählt und wie man den Pfad prüft.

## Was sich ändert — und was nicht

```text
VORHER (Provider-Stimme)
  Gerät ──Mikrofon──► Bridge ──► OpenAI GPT-Live ──► Audio ──► Bridge ──► Gerät
                                   (hört, denkt, spricht)

NACHHER (lokale Stimme)
  Gerät ──Mikrofon──► Bridge ──► OpenAI GPT-Live ──► Text  ──► Bridge ──► Piper ──► Gerät
                                   (hört, denkt)                (spricht lokal)
```

Unverändert bleiben: Firmware, Wakeword, Mikrofon, Display, Geräte-MCP,
Nachhör-Fenster, Session-Schlüssel, Tool-/Consult-Pfad und der gesamte
OpenClaw-Agent. **Nur das gesprochene Audio kommt nicht mehr vom Provider.**

Wichtig zu verstehen: OpenAI liefert weiterhin die **Erkennung** (Speech-to-Text)
und den **Gesprächstext**. Deshalb fällt weiterhin Provider-Nutzung an. Die
Stimme ist lokal, das Hören nicht.

Das Provider-Audio wird verworfen, nicht abgeschaltet — die OpenClaw-Sitzung
bietet keine reine Textausgabe an. Dieser Punkt ist bewusst so gebaut, damit
sich der Umbau jederzeit zurücknehmen lässt.

## Warum Piper

- Läuft ohne GPU auf der CPU; gemessen auf dem Referenzhost mit RTF 0,05–0,06.
- Das Modell bleibt im Bridge-Prozess geladen (Laden dauert ~1,3 s, danach
  ~70 ms bis zum ersten Ton).
- Deutsche Stimmen in guter Qualität, frei verfügbar.

Nicht geeignet auf diesem Host: autoregressive TTS-Modelle wie XTTS-v2, Bark
oder Orpheus. Sie erzeugen Audio Schritt für Schritt und sind auf vier
Niedrigenergie-Kernen nicht echtzeitfähig — das ist keine Einstellungssache.

## Stimme wählen

Die Stimme wird über zwei Schalter festgelegt:

```bash
--tts-local                      # lokale Sprachausgabe einschalten
--tts-voice de_DE-thorsten-medium   # Stimmenname
--tts-voice-dir ~/.local/share/piper-voices   # Stimmenverzeichnis
```

Alternativ ein direkter Pfad zur Datei:

```bash
--tts-model-path /pfad/zu/de_DE-thorsten-medium.onnx
```

### Stimmen besorgen

Zwei Wege:

```bash
# 1) Piper-Downloader (empfohlen)
python3 -m piper.download_voices de_DE-thorsten-medium

# 2) Paket der Distribution (Arch/CachyOS)
#    liefert einen Satz haeufiger Stimmen nach /usr/share/piper-voices
sudo pacman -S piper-voices-common
```

Die Stimme muss als `<name>.onnx` plus `<name>.onnx.json` vorliegen. Der
Dateiname ohne Endung ist der Wert für `--tts-voice`.

### Qualität einschätzen

```bash
python3 scripts/benchmark-tts.py
```

Erzeugt die Tabelle aus `VERSIONS.md` neu, auf der eigenen Hardware. Richtwert:
RTF unter 0,5 lässt im Betrieb genug Zeit für Netzwerk, Erkennung und Agent.

Qualitätsstufen (`x_low` → `low` → `medium` → `high`) betreffen den
Trainingsaufwand, nicht die Modellgröße. `medium` ist der Standard; `high` ist
nur bei den Thorsten-Stimmen vorhanden.

## Wie die Stimme getaktet wird

Das ist der technisch heikelste Teil und der Grund für ein eigenes Modul.

Das Gerät nimmt 60-ms-Opus-Rahmen in eine Warteschlange von **1,2 s**
(`MAX_DECODE_PACKETS_IN_QUEUE`). Ist die Warteschlange voll, verwirft der
Gerätecode weitere Rahmen **ohne zu blockieren**. Ein kompletter Satz in einem
Schwall würde also hörbare Lücken erzeugen.

`gateway/piper_speaker.py` wandelt Text in PCM und setzt die Abtastrate auf die
vom Gerät erwarteten 24 kHz um. `server.py` sendet das PCM dann **in
Echtzeit** in 60-ms-Rahmen, mit drei Rahmen Vorlauf, damit der Anfang nicht
unterläuft.

Zusätzlich wird Text an **Sprechgrenzen** gesprochen, nicht als ganzer Absatz.
So beginnt die Stimme früher. Wie kleinteilig das geschieht, steuert
`--tts-split` (`clause` / `sentence` / `whole`); Zahlzeichen trennen nie,
weder Dezimalpunkt (`13.95`) noch Uhrzeit (`13:52`). Vollständige Beschreibung
samt Stimm-Feinreglern: [`docs/08-LOKALE-ERKENNUNG.md`](08-LOKALE-ERKENNUNG.md).

## Prüfen, ob der lokale Pfad läuft

```bash
# 1) Stimme und Modul im Bridge-Venv
scripts/verify-host.sh

# 2) Einheitstests: Satztrennung, Synthese, Taktung
python3 tests/test_local_tts.py

# 3) Ende-zu-Ende: Bridge + lokale Stimme, ohne Gerät
python3 tests/test_bridge_e2e.py
```

Der Ende-zu-Ende-Test startet die Bridge mit einem Test-Doppel des Talk-Helfers,
verbindet sich als Gerät und prüft, dass der Nutzer-Transkript ankommt, lokales
Opus-Audio erzeugt wird und das Provider-Testaudio verworfen wird.

Im laufenden Betrieb zeigen die Bridge-Logs:

```text
Local voice enabled: loading /home/.../de_DE-thorsten-medium.onnx
Local voice ready: {'model': ..., 'source_rate': 22050, 'target_rate': 24000}
Local voice stream complete at 158400 PCM bytes; device will switch ...
```

## Zurück zur Provider-Stimme

Das Installationsskript kennt einen Schalter, der die lokalen Optionen wieder
entfernt:

```bash
scripts/install-host.sh --public-host 192.168.178.143 --provider-voice
```

Damit läuft die Bridge wie zuvor und spielt die OpenAI-Stimme ab. Es muss nichts
umgebaut werden — der Provider-Pfad ist im Code vollständig erhalten.

## Bekannte Grenzen

- **Das Hören bleibt beim Provider.** Dieser Aufbau ist kein rein lokaler
  Assistent. Eine vollständig lokale Erkennung wäre mit `faster-whisper`
  möglich (auf dem Referenzhost bereits als Wyoming-Dienst vorhanden), ist
  aber nicht Teil dieses Umbaus.
- **Provider-Audio wird verworfen, nicht vermieden.** Es entstehen weiterhin
  Audio-Token für die Ausgabe. Eine reine Textausgabe bietet die
  OpenClaw-Talk-Session derzeit nicht an.
- **Die erste Äußerung ist langsamer.** Das Piper-Modell lädt beim Start
  (~1,3 s). Danach ist es warm.
- **Stimmen müssen vorliegen.** Das Installationsskript lädt keine Stimme
  automatisch; es weist nur darauf hin, wenn eine fehlt.
- **Phonem-Warnungen sind kosmetisch.** Manche Stimmen (z. B. `eva_k`) melden
  beim Start `Missing phoneme from id map: …` für Zeichen, die ihre
  ID-Tabelle nicht kennt. Das Audio bleibt vollständig und korrekt (mit
  Umlauten, `ß` und Dezimalzahlen geprüft). Die Warnung stammt aus der
  Phonemisierung und ist kein Fehler des lokalen Pfads.
