# 11. Zweites Gerät: Waveshare ESP32-S3-AUDIO-Board

Zusätzlich zum **Touch-LCD-4B** (Hauptgerät, `docs/02-FIRMWARE.md`) gibt es ein
zweites Jarvis-Gerät: das **nackte** Waveshare **ESP32-S3-AUDIO-Board**
(SKU 32184/32185). Es nutzt **dieselbe Bridge** und **denselben Host** — es
unterscheidet sich nur in der Firmware.

> Die Firmware ist technisch unabhängig von Cloud- oder Lokal-STT/TTS. Für den
> hier beschriebenen Stand ist dieses lokale Repository maßgeblich. Eine Kopie
> im Cloud-Repository gilt nur dann als identisch, wenn die Firmwarebäume
> tatsächlich verglichen wurden; der Name des Repositories ist kein Beleg.

## Lokale Variante: was zu beachten ist

Die Firmware ist gleich — der **Host** unterscheidet sich:

| | Cloud-Variante | **Lokale Variante** |
|---|---|---|
| Erkennung | extern (OpenAI) | **lokal**, CPU (Canary/Piper) |
| Stimme | extern | **lokal**, CPU (Piper) |
| Anzeige des Boards | Ring-LEDs | Ring-LEDs (identisch) |
| Gesichtsanzeige des 4B | ja | ja (`jarvis_face_display.*`) — betrifft **nur** den 4B |

Zwei Punkte, die im lokalen Betrieb zählen:

1. **Der Sprechen-Wächter greift auch lokal.** Alle Audioausgaben laufen über
   dieselbe Sendestelle; der Wächter gegen ein hängendes „Sprechen" ist damit in
   beiden Varianten aktiv.
2. **Zwei Geräte gleichzeitig kosten CPU.** Erkennung und Stimme laufen lokal.
   Der Erkennungsdienst nimmt mehrere Verbindungen an (ein Handler je Gerät),
   aber beide teilen sich dieselbe Rechenleistung. Für den Normalbetrieb
   („ein Gerät pro Raum") ist das unkritisch; wer zwei Geräte **gleichzeitig**
   betreibt, sollte die Antwortzeit beobachten.

## Unterschied zum 4B

| | Touch-LCD-4B | ESP32-S3-AUDIO-Board |
|---|---|---|
| Anzeige | 4″-Display + animiertes Gesicht | **kein Display** |
| Rückmeldung | Gesicht auf dem LCD | **7 RGB-Ring-LEDs** (WS2812, GPIO 38) |
| Board-Ziel | `waveshare/esp32-s3-touch-lcd-4b` | `waveshare/esp32-s3-audio-board` |
| Firmware-Patch | `firmware/patches/xiaozhi-esp32-openclaw.patch` | `firmware/patches/jarvis-audio-board-ring.patch` |
| Watchdog / `playback_drained` | im großen Patch enthalten | **nicht** enthalten (siehe unten) |

Der Upstream kennt das Board bereits (`main/boards/waveshare/esp32-s3-audio-board/`).
Dieses Projekt ergänzt **nur** die Ring-Zustandssprache und korrigiert die
LED-Anzahl von 6 auf 7.

## Der Ring als einzige Ausgabe

Weil kein Bildschirm da ist, trägt der Ring die komplette Rückmeldung.
Leitidee: **Farbe = Rolle, Bewegung = Aktivität.**

| Zustand | Farbe | Bewegung | Bedeutung |
|---|---|---|---|
| Bereitschaft (idle) | blau, gedimmt | ruhiges Atmen | da, hört aber nicht mit |
| WLAN einrichten | blau | langsames Blinken | braucht Einrichtung |
| Verbinden | weiß | schnelles Blinken | Kontakt wird aufgebaut |
| **Zuhören** | **grün** | **Licht läuft im Kreis** | **jetzt sprechen** |
| Sprechen | cyan | Atmen | Antwort läuft |
| Update | violett | schnelles Laufen | nicht ausschalten |
| Aktivierung | bernstein | Blinken | einen Moment |
| Fehler | **rot, stehend** | — | etwas ist schief |

„Zuhören" ist bewusst die **einzige** Stufe mit *laufendem* Licht: aus der
Entfernung die auffälligste Bewegung, genau im richtigen Moment.

### Weißes Licht während der Kameraaufnahme

Wird ein Foto ausgelöst („Mach ein Foto", „Was siehst du da?"), leuchtet der
Ring für die Dauer der Aufnahme **volles Weiß (255, alle sieben LEDs, stehend)**
und kehrt danach selbsttätig in den vorherigen Gerätezustand zurück.

Zwei Zwecke in einem Signal:

- **Rückmeldung** — man sieht, wann das Bild im Kasten ist und kann den
  Gegenstand erst danach wegbewegen.
- **Ausleuchtung** — Weiß ist für Fotos die brauchbare Lichtfarbe; die Farbe
  des Gerätezustands (grün/cyan) wäre dafür ungeeignet.

Umsetzung an drei Stellen:

| Datei | Ergänzung |
|---|---|
| `main/led/led.h` | virtuelle `SetCameraFlash(bool)`, Standard wirkungslos |
| `main/mcp_server.cc` | im `self.camera.take_photo`-Handler: an vor `Capture()`, aus per RAII-Wächter |
| `jarvis_ring_led.{h,cc}` | setzt volles Weiß, unterdrückt `OnStateChanged()` solange aktiv, 30-s-Wachhund |

Zwei Details, die sonst stören würden:

- **Vorrang:** Solange die Aufnahme läuft, ignoriert der Ring `OnStateChanged()`.
  Sonst würde ein Zustandswechsel (z. B. „Sprechen", während das Modell schon
  antwortet) das weiße Licht sofort überschreiben.
- **Wachhund (30 s):** Bricht die Aufnahme ab, ohne dass die Freigabe kommt,
  stellt der Timer den Ring selbst zurück. Ohne ihn bliebe das Board dauerhaft
  weiß — und der Ring ist die einzige Anzeige dieses Boards.

Der Auslöser sitzt im Kamera-Werkzeug, nicht in der Bridge: Das Gerät weiß als
einziges, wann der Verschluss wirklich offen ist. Ein Signal von der Bridge
käme zu früh (vor dem Upload) oder zu spät (nach der Auswertung).

### Farbordnung: warum die Farben „vertauscht" wirkten

Der LED-Treiber (`esp_driver_led_strip`) ist auf **GRB** eingestellt
(`circular_strip.cc`: `LED_STRIP_COLOR_COMPONENT_FMT_GRB`) und füllt den Puffer
als `[grün, rot, blau]`. Der Ring **dieser** Platine arbeitet aber in
**RGB**-Reihenfolge (`led_strip_types.h`: GRB ist `r_pos=1, g_pos=0, b_pos=2`).

Ohne Anpassung landen Rot und Grün vertauscht — Blau bleibt richtig (Position 2
ist in beiden Ordnungen Blau):

| Beobachtung | Ursache |
|---|---|
| Blau korrekt | Blau liegt in beiden Ordnungen auf Position 2 |
| „Grün" leuchtet **rot** | R/G-Vertauschung |
| Cyan (Sprechen) erscheint **magenta/lila** | C = G+B → vertauscht M |

Deshalb laufen **alle** Farbwerte durch `PanelColor(r, g, b)`, das die beiden
Kanäle tauscht. Farben werden im Code weiter natürlich als Rot, Grün, Blau
angegeben. Dasselbe Vorgehen nutzen andere Waveshare-Boards im Upstream (z. B.
`esp32-c6-lcd-0.85`, `esp32-s3-lcd-0.85`): sie rufen ihre Farbhelfer ebenfalls
mit `(green, red, blue)`.

## Kamera

Das Board trägt eine **OV5640** (DVP, I²C-Adresse `0x3c`). Der Sensor wird beim
Boot automatisch erkannt; die OV2640 wird zuerst probiert und meldet
`get sensor ID failed` — das ist der erste Suchschritt, kein Fehler.

### Treiber: `esp32-camera`, nicht `esp_video`

Belegt am 25.10.2026: mit `esp_video` kam das Bild **magentafarben**, weil dieser
Treiber beim OV5640 nur Spiegeln/Drehen kennt, keinen Weißabgleich. Der klassische
`esp32-camera`-Treiber setzt den Sensor-Weißabgleich und liefert JPEG **direkt aus
dem Sensor**; die YUV-Wandlung im Software-Encoder entfällt.

Umgesetzt in `firmware/patches/jarvis-audio-board-camera-esp32camera.patch`
(Board-Datei + `config.json`). Der Build wendet ihn nach Ring- und PSRAM-Patch an.

### Auflösung: Leiter mit echter Frame-Prüfung

Hohe Auflösungen scheitern an der PSRAM-DMA, nicht am Sensor. Belegt:

| Auflösung | Rahmenpuffer | gemessener Frame | Ergebnis |
|---|---:|---:|---|
| 2048×1536 @ 20 MHz | 629.145 B | 597.382 B | `cam_hal: DMA overflow` |
| 1600×1200 @ 20 MHz | 384.000 B | 322.075 B | `cam_hal: DMA overflow` |
| **800×600 @ 12 MHz** | **96.000 B** | **~38.000 B** | **stabil** |

Entscheidend ist die Prüfung: eine Einstellung gilt erst als tauglich, wenn
**drei Frames hintereinander** ankommen. Ein einzelner Treffer beweist nichts —
bei 2048×1536 kam genau ein Frame knapp unter der Puffergröße durch und galt
damals fälschlich als Erfolg.

Der Treiber setzt den Strom außerdem selbst zurück, wenn er nach längerem
Leerlauf steht (`RecoverStream`, Logzeile `Camera stream restarted`).

### Kamera-Lage ohne Neubau

Dieselbe Platine kann die Kamera kopfüber oder gespiegelt tragen. Die Lage ist
deshalb **keine Firmware-Eigenschaft**, sondern Konfiguration:
`camera-orientation.json` im Bridge-Verzeichnis, Schlüssel ist die Device-Id.

```json
"90:70:69:eb:1e:b4": { "hmirror": false, "vflip": true }
```

Die Bridge sendet die Werte beim MCP-Handshake (`capabilities.vision`), das
Gerät setzt die Sensor-Register. **Kein Umrechnen des JPEG, keine Verzögerung.**
Änderungen greifen beim nächsten Verbinden (Weckwort), ohne Build.

| Beobachtung | Einstellung |
|---|---|
| auf dem Kopf **und** spiegelverkehrt | `vflip: true`, `hmirror: false` (netto nur vertikal gespiegelt) |
| nur auf dem Kopf | `hmirror: true`, `vflip: true` (180°-Drehung) |
| nur seitenverkehrt | `hmirror: true`, `vflip: false` |

Die Register dürfen **nicht bei laufendem JPEG-Strom** umgestellt werden — danach
fehlen SOI/EOI-Marker und das Bild bleibt aus (`NO-SOI - JPEG start marker
missing`). Deshalb setzt eine Änderung den Strom neu auf (dauert einige Sekunden;
der MCP-Handshake der Bridge wartet dafür bis zu 15 s). Eine **wiederholte**
gleiche Lage wird erkannt und kostet nichts.

### Hitze: Kamera läuft dauerhaft

Der Sensor ist ab dem Boot durchgehend aktiv (`streaming_on_`) und wird warm;
warme Sensoren rauschen sichtbar. Ein Abschalten nach dem Foto über
`esp_camera_deinit()` im laufenden Betrieb ist **gescheitert**: der Treiber gibt
Puffer doppelt frei (`assert failed: tlsf_free … block already marked as free` →
Boot-Schleife). Auch das vorherige Zurückgeben des Frames per
`esp_camera_fb_return()` half nicht.

Wer das angehen will, braucht eine belegte Messung (Heap-Verbrauch,
`deinit`-Semantik im Treiber) — nicht weitere Builds auf Verdacht. Ein
aussichtsreicher Weg wäre der Sensor-Software-Standby über Register `0x3008`
Bit 6, der keinen Heap anfasst.

**Notfall:** wenn ein Kamera-Build das Gerät in eine Boot-Schleife bringt, das
zuletzt bekannte gute App-Image direkt schreiben:

```bash
sudo .venv-esptool/bin/esptool --chip esp32s3 --port /dev/ttyACM0 \
  write-flash --flash-mode dio --flash-size 16MB --flash-freq 80m \
  0x20000 <bekannt-gutes-xiaozhi.bin>
```

## Bauen

```bash
./scripts/build-firmware-audio-board.sh 192.168.68.157
```

Das Skript

1. klont `78/xiaozhi-esp32` auf den gepinnten Commit aus `VERSIONS.md`,
2. wendet `firmware/patches/jarvis-audio-board-ring.patch` an (erst `--check`),
3. setzt den Host in `main/boards/waveshare/esp32-s3-audio-board/config.json`,
4. baut das Builder-Image und darin die Firmware mit `de-DE` und
   `--wake-word wn9_jarvis_tts`.

Der Host muss die Adresse sein, unter der das **Gerät** den Host erreicht
(Gerätenetz), nicht die LAN-Adresse des Hosts. Sonst aktiviert das Board gegen
`api.tenclass.net` und fordert einen Code.

## Flashen

> **Immer `--board audio` angeben.** Das 4B-Image und das Audio-Board-Image sind
> verschieden, beide Boards sind aber ESP32-S3 mit 16 MB Flash — der Chip verrät
> das Modell nicht. Das Skript bricht ab, wenn Image und Board nicht passen.

### Nur die App-Partition (WLAN/NVS bleiben erhalten)

```bash
./scripts/flash-firmware.sh --port /dev/ttyACM0 --board audio
```

Das ist der Standardweg: WLAN-Zugangsdaten, NVS und Assets bleiben unangetastet,
das Gerät verbindet sich danach **ohne** Neu-Provisionierung.

Das Skript kümmert sich um alles, was sonst leicht übersehen wird:

- installiert `esptool==5.4.0` in ein eigenes `.venv-esptool` (keine globale
  Version vorausgesetzt),
- prüft die Board-Kennung im Image gegen `--board`,
- prüft die Prüfsumme aus `CHECKSUMS.sha256` und die Partitionsgröße,
- schreibt nach `0x20000` und prüft danach mit `verify-flash`.

### Von Hand flashen (ohne Skript)

Nur wenn das Skript nicht benutzt werden soll. `esptool` muss dann selbst
vorhanden sein:

```bash
python3 -m venv .venv-esptool
.venv-esptool/bin/python -m pip install 'esptool==5.4.0'

sudo .venv-esptool/bin/esptool --chip esp32s3 --port /dev/ttyACM0 -b 460800 \
  write-flash --flash-mode dio --flash-size 16MB --flash-freq 80m \
  0x20000 firmware/prebuilt/audio-board/xiaozhi.bin
```

`sudo` ist nötig, weil `/dev/ttyACM0` üblicherweise `root:uucp` gehört und der
Benutzer oft nicht in `uucp` ist. Wer die Gruppe ergänzt (`sudo usermod -aG uucp
$USER`, danach neu anmelden), kann auf `sudo` verzichten.

**Nach dem Schreiben immer prüfen** — ein Flash ohne Verify ist kein Nachweis:

```bash
sudo .venv-esptool/bin/esptool --chip esp32s3 --port /dev/ttyACM0 \
  verify-flash 0x20000 firmware/prebuilt/audio-board/xiaozhi.bin
```

### Vollimage: im Repo nicht vorhanden

Für das Audio-Board gibt es **kein** veröffentlichtes Vollimage. Auf einem
leeren Board reicht die App-Partition nicht — Bootloader, Partitionstabelle und
Assets fehlen. Dann erst bauen:

```bash
./scripts/build-firmware-audio-board.sh <HOST_IM_GERAETENETZ>
```

Das Ergebnis liegt unter `build/firmware/merged-binary.bin`. Erst danach wäre
`--board audio --full` sinnvoll; das Skript weist darauf hin, solange das Image
fehlt.

### Boot prüfen

Nach dem Flashen das Log bei 115200 Baud mitlesen. Erwartet:

```text
I (208) Board: UUID=… SKU=esp32-s3-audio-board
I (768) MCP: Add tool: self.camera.take_photo
I (…) WifiStation: connected with <SSID>
I (…) StateMachine: State: activating -> idle
```

Bleibt es bei `State: starting` stehen, ist meist die NVS-OTA-URL falsch (siehe
unten) oder der Host ist aus dem Gerätenetz nicht erreichbar.

### Partitionen des Boards

| Partition | Offset | Größe |
|---|---:|---:|
| NVS | `0x9000` | 16 KiB |
| OTA data | `0xd000` | 8 KiB |
| PHY init | `0xf000` | 4 KiB |
| App OTA 0 | `0x20000` | 4032 KiB |
| App OTA 1 | `0x410000` | 4032 KiB |
| Assets | `0x800000` | 8 MiB |

### WLAN/OTA-URL ohne Neubau setzen

Die OTA-URL steht in der **NVS** (`namespace wifi`, Schlüssel `ota_url`) und hat
**Vorrang** vor `CONFIG_OTA_URL` aus der Firmware. Für ein anderes Netz reicht
also ein NVS-Image mit dem passenden Wert, geschrieben nach `0x9000`:

```bash
# nvs_partition_gen.py aus ESP-IDF; CSV mit ssid/password/ota_url/board.uuid
python "$IDF_PATH/components/nvs_flash/nvs_partition_generator/nvs_partition_gen.py" \
  generate wifi.csv nvs.bin 0x4000
esptool --chip esp32s3 --port /dev/ttyACM0 write-flash 0x9000 nvs.bin
```

## Veröffentlichter Anker

`firmware/prebuilt/audio-board/xiaozhi.bin` ist die **App-Partition** (2.833.056 B),
nicht das Vollimage. Sie wurde partition-weise geprüft: **keine** echten
Zugangsdaten enthalten (WLAN-Name/Passwort stehen ausschließlich in der NVS, die
nicht im Repo liegt). Die `password`/`ssid`/`ota_url`-Treffer im String-Scan sind
Code-Konstanten (WLAN-Fehlermeldungen, JSON-Schlüssel, die Einrichtungs-Webseite);
`-----BEGIN … PRIVATE KEY-----` sind OpenSSL-Stringtabellen aus dem TLS-Stack,
keine eingebetteten Schlüssel.

## Tasten am Gerät

Die nackte Platine hat fünf Taster. Zwei davon steuern jetzt die Lautstärke.

| Taste | Anschluss | Funktion in dieser Firmware |
|---|---|---|
| **K1** | TCA9555 **EXIO9** | **lauter** (+10 % pro Druck) |
| **K2** | TCA9555 **EXIO10** | frei (im Waveshare-Demo: MP3-Wiedergabe, hier nicht vorhanden) |
| **K3** | TCA9555 **EXIO11** | **leiser** (−10 % pro Druck) |
| BOOT | GPIO0 | Gespräch umschalten; beim Start: WLAN-Einrichtung |
| RESET | CHIP_PU | Neustart |

Die drei Tasten hängen **nicht** am ESP32, sondern am I/O-Expander TCA9555.
Deshalb liest sie ein eigener Treiber (`iot_button` mit eigener
`get_key_level`-Funktion) statt der GPIO-Variante — dasselbe Verfahren nutzt
das Upstream-Board `df-k10`.

Die Lautstärke wird in 10-%-Schritten verändert und auf 0…100 % begrenzt.
Jeder Druck wird im seriellen Log protokolliert:

```text
waveshare_s3_audio_board: Volume up -> 80%
```

Beim Start meldet die Firmware, ob die Tasten bereit sind:

```text
waveshare_s3_audio_board: K1 volume-up ready
waveshare_s3_audio_board: K3 volume-down ready
```

Quelle der Pin-Zuordnung: Waveshare-Pinout und Schaltplan
(`ESP32-S3-AUDIO-Board_1.1.pdf`, Abschnitt „Key").

Die Tasten funktionieren in **jedem** Gerätezustand — `ChangeVolume()` prüft
den Zustand nicht, und der `iot_button`-Treiber pollt unabhängig von der
Gesprächslogik. Der Wert wird in der NVS (`audio` / `output_volume`) gespeichert
und überlebt einen Neustart.

## Lautstärke-Anzeige im Ring

Weil das Board kein Display hat, zeigt der Ring den Pegel selbst: `ShowVolume()`
füllt die **sieben LEDs einzeln** als Balken und färbt ihn nach Höhe.

| Pegel | LEDs | Farbe |
|---|---|---|
| 1–25 % | 1–2 | grün |
| ~30–70 % | 3–5 | gelb (Übergang) |
| 75–100 % | 6–7 | rot |

Nach **1,4 Sekunden** kehrt der Ring automatisch in den normalen Gerätezustand
zurück (blau atmend = Bereitschaft, grün laufend = Zuhören, cyan atmend =
Sprechen). So bleibt die Zustandssprache erhalten, der Balken ist nur eine
kurze Rückmeldung.

Technisch: `CircularStrip::SetMultiColors()` setzt alle LEDs in einem Zug; ein
`esp_timer` mit einmaliger Laufzeit stellt danach über `OnStateChanged()` den
Gerätezustand wieder her.

## Lautstärke-Kennlinie (warum das Board zu leise war)

Der Standard des Treibers bildet **100 % auf 0 dB** ab — der ES8311 kann aber
bis **+32 dB**. Mit dem kleinen Lautsprecher dieser Platine war deshalb selbst
95 % noch leise.

Diese Firmware setzt in `box_audio_codec.cc` eine eigene Kennlinie:

| Prozent | Standard-Treiber | **diese Firmware** |
|---|---|---|
| 0 % | −50 dB | −50 dB |
| 50 % | −25 dB | −15 dB |
| 70 % | −15 dB | −1 dB |
| 100 % | 0 dB | **+20 dB** |

Der untere Bereich bleibt unverändert (leise stellen funktioniert weiter), nur
die Obergrenze steigt — rund **20 dB mehr Spitzenpegel** bei gleichem
Prozentwert.

Das erklärt auch ein beobachtetes Verhalten: stand der Wert schon auf 95 %,
brachte „lauter" nur noch 5 Punkte (≈2,5 dB, kaum hörbar), während „leiser"
95 Punkte Spielraum hatte. Mit einem Startwert von 70 % ist in beide
Richtungen Luft.

## Meldung des Wiedergabe-Endes (`playback_drained`)

Das Board meldet der Bridge jetzt selbst, wann der letzte Ton physisch durch ist.
Das ist der Grund, warum der Ring nach einer Antwort **sofort** wieder auf grün
(Zuhören) springt.

Früher fehlte diese Meldung: Die Bridge konnte die Sprechdauer nur aus der
Bytezahl schätzen (48 000 B/s bei 24 kHz, 16 Bit, mono) und wartete danach
unnötig — im Log sichtbar als
`No playback_drained from device; opening follow-up window anyway (waited 9.7s)`.
Bei einem Witz waren das ~10 Sekunden Verzögerung bis „Zuhören".

Der Patch ergänzt dafür drei Dinge:

| Datei | Ergänzung |
|---|---|
| `main/protocols/protocol.{h,cc}` | `SendPlaybackDrained()`, `SendDeviceState()` |
| `main/application.h` | Merker `tts_stream_end_pending_` |
| `main/application.cc` | `stream_end`-Behandler; Meldung bei `MAIN_EVENT_PLAYBACK_DRAINED`; Zustandsmeldung bei jedem Zustandswechsel |

Ablauf: Der Server sendet `tts stream_end`. Ist die Warteschlange schon leer,
geht `playback_drained` sofort raus; sonst wird der Merker gesetzt und die
Meldung folgt, sobald das Audio wirklich abgespielt ist.

Der Zeitgeber in der Bridge bleibt als Sicherheitsnetz bestehen (für Geräte ohne
diese Meldung), greift aber bei diesem Image nicht mehr.

## Was das Board weiterhin **nicht** hat

Der **Wiederherstellungs-Watchdog** aus dem großen 4B-Patch
(`firmware/patches/xiaozhi-esp32-openclaw.patch`) ist hier nicht enthalten. Er
ist auch nicht nötig: Die Bridge schließt eine hängende Sprachausgabe nach 45
Sekunden ohne neue Audiodaten selbst.

## Bekannte Fallstricke

1. **Endlos-Rekursion (Absturz).** `CircularStrip::SetBrightness()` ruft intern
   `OnStateChanged()` auf. Ein `SetBrightness()` **innerhalb** von
   `OnStateChanged()` ergibt eine Endlos-Rekursion
   (`Guru Meditation / LoadProhibited`). Helligkeit daher **einmalig im
   Konstruktor** setzen.
2. **Falsches Netz in der OTA-URL.** Siehe oben — das Board hängt im
   Gerätenetz; eine LAN-Adresse ist für es unerreichbar.
3. **Serial-Log braucht Rechte.** `/dev/ttyACM0` gehört `root:uucp`. Ohne
   `uucp`-Gruppe ist nur `sudo` möglich.

## Belegter Referenzbuild (2026-10-17)

```text
Board   : waveshare/esp32-s3-audio-board  (de-DE, wn9_jarvis_tts)
IDF     : ESP-IDF v6.1  (espressif/idf:v6.1)
Status  : exit 0
xiaozhi.bin        sha256 1ff558d40d3130987b5b0f44d5c773b23e23b77f786f362dad1d1ad6167ce86c
merged-binary.bin  sha256 2c780ec3d0b06eee00259d58ccd7185c95d3435adda680cf1f6854155533b761
Backup vorher     : vollständiges 16-MB-Image des Geräts
```

Nachweis, dass der Ring-Code im Image steckt: `jarvis_ring_led.cc.obj` wird
kompiliert, und die Sprach-/Zustandstexte (`Bereitschaft`, `Sprechen`) liegen im
App-Binary.
