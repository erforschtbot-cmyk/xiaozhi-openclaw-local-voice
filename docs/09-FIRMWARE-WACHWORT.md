# Firmware: Wachwort nicht mehr mitsenden

Dieses Kapitel erklärt den **einzigen** Eingriff in der Firmware und warum er
risikoarm ist.

## Das Problem

Das Gerät öffnet den Audiokanal erst, wenn es das Wachwort gehört hat. Je nach
Zeitpunkt liegen die ersten Audiorahmen noch **davor** — das Wachwort steckt
dann mit im Transkript. Die lokale Erkennung schrieb deshalb Sätze wie:

```
"Jarvis."                    → reines Wachwort, als Frage verschickt
"Job es."                    → Fehldeutung von "Jarvis"
"Ja, bis da an den Witz."    → "Jarvis, erzähl..." + Auftrag
```

Der Host kann das zwar herausfiltern (`cleanTranscript`, siehe
`docs/08-LOKALE-ERKENNUNG.md`), aber besser ist, die Rahmen gar nicht erst zu
verschicken.

## Der Schalter in der Firmware

Die Ursprungsfirmware sendet das Wachwort-Audio an den Server
(`main/application.cc`):

```c
#if CONFIG_SEND_WAKE_WORD_DATA
    // Encode and send the wake word data to the server
    while (auto packet = audio_service_.PopWakeWordPacket()) {
        protocol_->SendAudio(std::move(packet));   // <- die stoerenden Rahmen
    }
    protocol_->SendWakeWordDetected(wake_word);
    SetListeningMode(GetDefaultListeningMode());
#else
    // Set flag to play popup sound after state changes to listening
    play_popup_on_listening_ = true;
    SetListeningMode(GetDefaultListeningMode());
#endif
```

`CONFIG_SEND_WAKE_WORD_DATA` ist im Upstream standardmäßig **`y`**. Genau
deshalb sendet das Referenzgerät die Rahmen und deshalb standen „Job es." und
„Ja, das…" im Transkript. Belegt im Build des Referenzgeräts:
`build/config/sdkconfig.h` enthielt `#define CONFIG_SEND_WAKE_WORD_DATA 1`.

## Was deaktiviert wird — und was ausdrücklich nicht

| Betroffen? | |
|---|---|
| Mikrofon | **nein** |
| I2S / Audio-Codec | **nein** |
| Wachwort-Erkennung (ESP-SR) | **nein** — läuft vor dem `#if`, in beiden Zweigen |
| Mithören (`SetListeningMode`) | **nein** — steht in beiden Zweigen |
| Gesendete Rahmen | **nur das** |

Der `#else`-Zweig ist ein **vorgesehener** Weg, kein Notbehelf. Er spielt beim
Aktivieren einen kurzen Ton statt Audio zu senden.

Auf der Host-Seite hängt nichts an der Nachricht `SendWakeWordDetected`; die
Bridge startet ihren Helfer über den `listen`-Status. Das wurde geprüft
(`grep` in `gateway/server.py`). Der Wegfall ist damit für den Ablauf
folgenlos.

## Wo der Schalter gesetzt wird

Im Patch `firmware/patches/xiaozhi-esp32-openclaw.patch`, in der
Board-Konfiguration. Dort steht bereits der OTA-Eintrag:

```json
"name": "esp32-s3-touch-lcd-4b",
"sdkconfig_append": [
    "CONFIG_USE_WECHAT_MESSAGE_STYLE=n",
    "CONFIG_USE_DEVICE_AEC=y",
    "CONFIG_OTA_URL=\"http://192.168.178.143:8766/xiaozhi/ota/\"",
    "CONFIG_SEND_WAKE_WORD_DATA=n"
]
```

`sdkconfig_append` ist der vorgesehene Weg des Build-Systems für
Board-Konfiguration; `scripts/build.py` übernimmt die Werte in die
Kompilierung. Im Build-Log erscheint die Zeile als
`sdkconfig_append: CONFIG_SEND_WAKE_WORD_DATA=n`.

## Bauen und flashen

```bash
# 1. Firmware bauen (Docker, dauert ~10 Minuten)
scripts/build-firmware.sh --public-host 192.168.178.143

# 2. ERST sichern, dann flashen
scripts/backup-device-firmware.sh --port /dev/ttyACM0 --baud 921600
scripts/flash-firmware.sh --port /dev/ttyACM0          # nur App-Partition
```

`flash-firmware.sh` schreibt ausschließlich bei `0x20000` und liest danach
zurück. **WLAN-Zugangsdaten und Assets bleiben erhalten.** Der Vollweg
(`--full-image-i-understand`) wäre nur bei beschädigtem Flash nötig und löscht
das NVS.

## Prüfen

```bash
# Läuft die neue Firmware? (Compile-Zeit und ELF-Hash vergleichen)
sudo python3 scripts/read-device-info.py --port /dev/ttyACM0

# Erwartet (Stand 2026-09-28):
#   compile    : 13:08:52 Sep 28 2026
#   elf-sha256 : c30048b5f32545b3d596fb45089a0ab2e0db6b29d94cefdd724f023e11f937cd
```

Nach dem Flashen verbindet sich das Gerät neu. Im Bridge-Log darf das
Wachwort dann nicht mehr als Transkript auftauchen.

## Rückweg

Das Vorgänger-Image liegt als `firmware/prebuilt/previous/xiaozhi.bin`
(ELF `77fd991a…`). Zurück:

```bash
esptool --chip esp32s3 --port /dev/ttyACM0 write-flash 0x20000 \
  firmware/prebuilt/previous/xiaozhi.bin
```

## Beobachtete Warnungen im Build (harmlos)

Der Build meldet für `CONFIG_SR_WN_*` und einige ESP-IDF-Optionen
„`default False` is not a valid bool value … Value is treated as 'n'". Das ist
ein bekannter Schönheitsfehler der Upstream-Kconfig-Dateien und hat **keine**
Auswirkung auf den Build — er lief mit `exit_code 0` durch.
