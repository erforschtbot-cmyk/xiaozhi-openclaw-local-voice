# Foto per Sprache (Ist-Stand 2026-10-20)

Der Nutzer sagt z. B. „Mach mal ein Foto" oder „Was siehst du?". Das Gerät
fotografiert, das Bild geht an OpenClaw, die Antwort wird gesprochen.

## Was bereits im Gerät steckt (kein Firmware-Build nötig)

Die geflashte Audio-Board-Firmware (`2.5.0`, `...-jarvis-drained`) enthält:

- `self.camera.take_photo` als MCP-Werkzeug (`main/mcp_server.cc`),
- den Kamera-Pfad `EspVideo` (`main/boards/common/esp_video.cc`),
- OV2640/OV5640-Autodetektion (`main/boards/waveshare/esp32-s3-audio-board/config.json`),
- die Kamera-Stromversorgung über den TCA9555 (EXIO5 Reset, EXIO6 Enable).

Beleg am Image: `strings xiaozhi.bin | grep -E "take_photo|Image explain URL"`.

Die Kamera-Pins im Board-Code (`config.h`) entsprechen dem Waveshare-Pinout:

| Signal | GPIO |
|---|---|
| XCLK | 43 |
| PCLK | 44 |
| VSYNC | 21 |
| HREF | 1 |
| D0–D7 | 2, 17, 18, 39, 45, 46, 47, 48 |

## Was auf dem Host fehlte

1. **Vision-URL.** Das Gerät erfährt die Upload-Adresse nur über
   `MCP initialize` → `capabilities.vision.url`. Die Bridge schickte keine —
   das Werkzeug meldete `Image explain URL or token is not set`.
2. **Upload-Endpunkt.** Niemand nahm das JPEG entgegen.
3. **Bild-Auswertung.** Kein Weg, das Foto samt Frage an den Agenten zu geben.

## Umsetzung in der Bridge

`gateway/server.py`:

- `photo_endpoint()` — meldet dem Gerät
  `http://<lokale WebSocket-Adresse>:<ota-port>/photo`.
  Bewusst die Adresse der bestehenden Verbindung, damit es im Gerätenetz liegt.
- `initialize_mcp()` übergibt `capabilities.vision` mit URL und Token.
- Der OTA-HTTP-Server verarbeitet jetzt auch `POST /photo` und kann
  **HTTP/1.1-chunked** lesen (die Firmware lädt chunked hoch).
- `_split_multipart()` trennt `question` und `file`; das JPEG bleibt bytegenau.
- `run_photo_agent()` startet `photo-consult.mjs`, zeigt und spricht die Antwort.

`gateway/photo-consult.mjs`:

- eigener Gateway-Client, `chat.send` mit `attachments` (Base64-JPEG),
- Session `agent:voice:xiaozhi-consult-v6`,
- gibt den Antworttext auf stdout aus.

## Zwei Fallen, die im Code stehen

**1. Kein Warten im Upload.** Der HTTP-Aufruf des Geräts muss sofort
zurückkehren. Der Agent braucht Sekunden bis Minuten; ein synchrones Warten
liefe in den Geräte-Timeout und das Werkzeug meldete `Failed to upload photo`.
Deshalb antwortet `/photo` sofort („Einen Moment, ich schaue mir das Bild an.")
und die Auswertung läuft als Task über die offene WebSocket-Session.

**2. Kein Provider-Audio während der Foto-Runde.** Ein zwischengesprochenes
„Einen Moment" des Realtime-Modells würde die Bildantwort überdecken. Solange
`photo_speaking` gesetzt ist, werden Provider-Audio und das generische
`stream_end` verworfen. Der Merker wird in `finally` freigegeben — sonst bliebe
die nächste normale Antwort stumm.

## Auslöser (`PHOTO_PATTERN`)

Nur ausdrückliche Foto-/Bild-Absichten, u. a.:

- „mach/mache/nimm/schiess/knips/schick … Foto/Bild"
- „Foto/Bild … machen/aufnehmen/schicken"
- „was siehst/erkennst du"
- „schau mal/dir", „sieh dir das an"

Bewusst eng, damit „mach das Licht an" nicht hier landet.

## Belegter Fehler am Gerät und seine Behebung (2026-10-20, 02:49)

Der erste echte Sprachtst (`„Mach ein Foto"`, Board `90:70:69:eb:1c:0c`)
lieferte **zwei** Antworten:

1. Korrekt: „Auf dem Bild ist ein Laptop-Display mit einer dunklen Textansicht
   zu sehen, daneben auf einem Regal ein schnurloses Telefon und Kabel."
   (Foto 640×480, 50.556 B, bytegenau gespeichert, gesprochen mit Piper.)
2. Falsch, ~8 s später: „Die Kamera am Rechner reagiert gerade nicht, das Bild
   kam schwarz an."

Ursache der zweiten Antwort: Das Realtime-Modell startete für denselben Satz
einen eigenen `openclaw_agent_consult`. Dieser Lauf kennt das Foto **nicht** und
erfindet eine Fehlermeldung. Dieselbe Falle war für den Smart-Home-Direktweg
schon gelöst (`isSmartHomeFastPath`), für Fotos fehlte das Gegenstück.

Behebung:

- `openclaw-talk-realtime.mjs`: `isPhotoRequest()` unterdrückt den Consult für
  Foto-Äußerungen und antwortet „Das Foto wurde bereits aufgenommen und
  ausgewertet. Führe kein Werkzeug aus und beschreibe kein Bild." — analog zum
  Smart-Home-Fall, inklusive `tool_call_suppressed` mit `reason: photo_request`.
- `server.py`: `arm_photo_guard()` begrenzt eine Foto-Runde auf 60 s. Ohne das
  bliebe `photo_speaking` bei fehlendem Upload dauerhaft gesetzt, jedes
  Provider-Audio würde verworfen und die **nächste** Antwort bliebe stumm.
  Zusätzlich gibt eine neue Äußerung (`user_transcript`) einen hängenden
  Merker frei.

Das Foto ist gegen das Original geprüft: echtes Schreibtischbild, kein
Schwarzbild (`magick identify`: mean 0,44, std 0,26).

## Läuft die Firmware auch ohne Kamera?

**Ja.** Die Kamera ist optional — ein Gerät ohne Sensor ist ein vollwertiger
Sprachassistent, nur ohne Foto-Funktion. Geprüft im Quellbaum (2026-10-20):

### Kein harter Abbruch im Kamera-Pfad

`EspVideo::EspVideo()` (`main/boards/common/esp_video.cc`) steigt bei jedem
Fehler still aus, ohne `ESP_ERROR_CHECK`, ohne `abort()`:

```c
EspVideo::EspVideo(const esp_video_init_config_t& config) {
    if (esp_video_init(&config) != ESP_OK) {
        ESP_LOGE(TAG, "esp_video_init failed");
        return;                      // sauberer Ausstieg
    }
    video_fd_ = open(video_device_name, O_RDWR);
    if (video_fd_ < 0) {
        ESP_LOGE(TAG, "open %s failed, errno=%d(%s)", ...);
        return;                      // sauberer Ausstieg
    }
```

Der Board-Konstruktor ruft `InitializeCamera()` auf und macht danach mit
Display, Backlight und Netzwerk weiter. Ohne Sensor bleibt nur
`streaming_on_ = false`.

### Was der Nutzer merkt

`Capture()` bricht sofort ab:

```c
if (!streaming_on_ || video_fd_ < 0) {
    ESP_LOGE(TAG, "Capture failed: camera did not initialize ...");
    return false;
}
```

Das Werkzeug meldet `Failed to capture photo`. Die Bridge antwortet dann
„Das Foto hat nicht geklappt. Bitte noch einmal versuchen." — ehrlich, ohne
erfundene Bildbeschreibung. Sprache, Wakeword, WLAN und alle anderen
Funktionen laufen unverändert.

### Unterschied zwischen den beiden Boards

| Board | ohne Kamera |
|---|---|
| **4B** (Touch-LCD) | nutzt `Camera* Board::GetCamera() { return nullptr; }` → `self.camera.take_photo` wird **gar nicht angeboten** |
| **Audio-Board** | `camera_ = new EspVideo(...)` wird **immer** erzeugt → Werkzeug wird angeboten und schlägt fehl |

Die zweite Zeile ist kein Absturz, aber eine Unschönheit: Das Modell könnte
„ich schaue mal" ankündigen und dann scheitern. Wer das ändern will, lässt
`GetCamera()` erst ein Objekt liefern, wenn `streaming_on_` gesetzt ist
(kleiner Firmware-Patch plus Neubau, nicht Teil dieses Stands).

### Herkunft des Kamera-Codes

Die Kamera-Unterstützung ist **upstream**, nicht Teil dieses Projekts: Der
Audio-Board-Patch enthält **keine** hinzugefügten Zeilen für `EspVideo`,
`InitializeCamera` oder `CONFIG_CAMERA_*` (geprüft: 0 Treffer). Damit gilt das
Verhalten für jeden, der diese Board-Definition baut — mit oder ohne Kamera.

## Bekannte Grenzen

- **Antwort nur hörbar, wenn Piper verfügbar ist.** Der Realtime-Weg hat keine
  lokale Stimme; die Foto-Antwort wird mit der installierten Piper-Stimme
  gesprochen (`_photo_speaker()`). Fehlt Piper, bleibt der Text nur im Log.
- **Der Text wird als `stt` gesendet** (Geräteanzeige/Untertitel), nicht als
  Assistant-Transkript der Session.
- **Fotos landen im Bridge-Verzeichnis** als `photo-<MAC>-<Zeitstempel>.jpg`.
  Sie sind Laufzeitdaten und gehören nicht ins Repo.
- **Gesichtserkennung ist nicht Teil dieses Stands.**

## Prüfen

```bash
# Firmware kann Fotos:
strings firmware/prebuilt/audio-board/xiaozhi.bin | grep -c "take_photo"

# Endpunkt antwortet (ohne Gerät):
#   erwartet HTTP 200; ohne aktive Sitzung "keine Sprachverbindung aktiv"
curl -sS -o /dev/null -w 'HTTP %{http_code}\n' -X POST \
  -H "Device-Id: <MAC>" -H "Content-Type: multipart/form-data; boundary=X" \
  --data-binary @bild.bin http://<host>:8766/photo

# Am Gerät: „Jarvis, mach ein Foto" — Logzeilen
#   Photo requested: ...
#   Photo from ... bytes=...
#   Photo answer: ...
#   Photo answer spoken (N PCM bytes)
```

## Offen (Stand 2026-10-20)

Am Gerät **noch nicht** durchgesprochen: Das Board war beim Einbau nicht im
WLAN (MAC `90:70:69:eb:1c:0c` nicht im ARP). Der HTTP-Weg und der Bild-Consult
zum Agenten sind getrennt belegt:

- Multipart-Parser und chunked-Leser offline bytegenau geprüft,
- `/photo` liefert HTTP 200 in ~1,4 ms und speichert das JPEG bytegleich,
- `photo-consult.mjs` beschreibt ein Testfoto korrekt (echter Agentenlauf).
