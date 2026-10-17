# 7. Zweites Gerät: Waveshare ESP32-S3-AUDIO-Board

Zusätzlich zum **Touch-LCD-4B** (Hauptgerät, `docs/02-FIRMWARE.md`) gibt es ein
zweites Jarvis-Gerät: das **nackte** Waveshare **ESP32-S3-AUDIO-Board**
(SKU 32184/32185). Es nutzt **dieselbe Bridge** und **denselben Host** — es
unterscheidet sich nur in der Firmware.

> Die Firmware liegt in **beiden** Repositories identisch
> (`xiaozhi-openclaw-voice-terminal` und `xiaozhi-openclaw-local-voice`) — genau
> wie beim 4B. Es gibt keine Cloud-/Lokal-Variante des Board-Images.

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

### Nur die App-Partition (WLAN/NVS bleiben erhalten)

```bash
esptool --chip esp32s3 --port /dev/ttyACM0 -b 460800 \
  write-flash --flash-mode dio --flash-size 16MB --flash-freq 80m \
  0x20000 firmware/prebuilt/audio-board/xiaozhi.bin
```

Das ist der Standardweg: WLAN-Zugangsdaten, NVS und Assets bleiben unangetastet,
das Gerät verbindet sich danach **ohne** Neu-Provisionierung.

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

`firmware/prebuilt/audio-board/xiaozhi.bin` ist die **App-Partition** (2.830.576 B),
nicht das Vollimage. Sie wurde partition-weise geprüft: **keine** echten
Zugangsdaten enthalten (WLAN-Name/Passwort stehen ausschließlich in der NVS, die
nicht im Repo liegt). Die `password`/`ssid`/`ota_url`-Treffer im String-Scan sind
Code-Konstanten (WLAN-Fehlermeldungen, JSON-Schlüssel, die Einrichtungs-Webseite);
`-----BEGIN … PRIVATE KEY-----` sind OpenSSL-Stringtabellen aus dem TLS-Stack,
keine eingebetteten Schlüssel.

## Was das Board **nicht** hat

Dem Board-Image fehlen zwei Eingriffe, die das 4B-Image mitbringt:

1. **`playback_drained` / Zustandsmeldungen.** Das Board meldet das physische
   Ende der Wiedergabe **nicht** an die Bridge. Die Bridge fängt das mit einem
   Zeitgeber ab: sie öffnet das Nachhör-Fenster nach der geschätzten
   Wiedergabedauer selbst.
2. **Der Wiederherstellungs-Watchdog** aus dem großen Patch.

Beides ist **kein** Fehler des Boards, sondern schlicht nicht Teil dieses
Patches. Wer diese Eingriffe auch auf dem Board will, muss sie aus
`firmware/patches/xiaozhi-esp32-openclaw.patch` übernehmen und neu bauen.

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
xiaozhi.bin        sha256 0336d2f036e03128710c2185115af92a537db28b86c34496330be360066da231
merged-binary.bin  sha256 e77eede940f52c37daee7175893e0382938ad5034d6bd245298e1610d547ff56
Backup vorher     : vollständiges 16-MB-Image des Geräts
```

Nachweis, dass der Ring-Code im Image steckt: `jarvis_ring_led.cc.obj` wird
kompiliert, und die Sprach-/Zustandstexte (`Bereitschaft`, `Sprechen`) liegen im
App-Binary.
