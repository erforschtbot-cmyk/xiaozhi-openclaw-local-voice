# 15. Anzeigen auf dem Display (Karten)

Das 4B zeigt standardmäßig ein Gesicht. Daneben kann es **beliebige Karten**
anzeigen — Wetter, Listen, Statusmeldungen und Suchbilder. Gebaut wird auf dem
Host, das Gerät holt sich nur die fertigen RGB565-Pixel.

## Warum kein HTML

Auf dem ESP32-S3 läuft kein Browser und läuft auch keiner. HTML zu parsen, CSS zu
layouten und Javascript auszuführen ist auf 240 MHz mit 512 KB internem RAM nicht
machbar. Die Anzeige besteht deshalb aus **RGB565-Rohdaten in Displaygröße**
(480 × 480, exakt 460.800 Byte), die auf dem Host gerendert und über HTTP
abgeholt werden. PNG dient nur als Host-Vorschau. Nichts davon wird im Flash des
Geräts gespeichert.

## Aufbau

```
Vorlage (Python)  ->  render.py  ->  RGB565 .raw 480x480
                                        |
                                        v
                          jarvis-screen.service  (HTTP :8770)
                                        |
                          show.py  -----+---> Bridge :8766 /screen
                                                     |
                                                     v
                                          self.screen.show  ->  Display
```

| Teil | Ort |
|---|---|
| Veröffentlichte Quellen | `screen/` |
| Vorlagen (installiert) | `~/.local/share/jarvis-screen/templates/*.py` |
| Renderer (installiert) | `~/.local/share/jarvis-screen/render.py` |
| HTTP-Auslieferung | `serve.py`, systemd-Unit `jarvis-screen` |
| Alltagsbefehl | `~/.local/share/jarvis-screen/show.py` |
| Skill im Repo / installiert | `openclaw/skills/display-cards/` / `~/.openclaw/workspace-voice/skills/display-cards/` |
| Firmware-Werkzeuge | `firmware/patches/jarvis-screen-show.patch` |

## Benutzen

```sh
cd ~/.local/share/jarvis-screen
./show.py wetter --data '{"titel":"Morgen","ort":"Arnsberg","temp":18,
  "lage":"leichter Regen","spanne":"6–19 °C","symbol":"regen",
  "zeilen":[["Vormittag","12","bewölkt"],["Nachmittag","19","Schauer"]]}'
```

- `--no-push` — nur rendern, nicht senden
- `--hide` — Vollbild ausblenden
- `--file /pfad/bild.png` — fertiges Bild senden
- `--device <MAC>` — bei mehreren verbundenen Geräten das Ziel wählen

Status: `systemctl --user status jarvis-screen`.
Einzelbild direkt: `http://<host>:8770/img/wetter.png`.

Der Host-Installer installiert `screen/`, eine eigene Pillow-Umgebung, die
systemd-Unit und den Voice-Skill. Auf einem bestehenden Host:

```sh
bash scripts/install-host.sh --public-host <HOST-IP>
```

## Geräte ohne Display

Die Bridge fragt nach dem MCP-Start die vollständige, gegebenenfalls
paginierte Werkzeugliste jedes Geräts ab. Nur eine Sitzung, die
`self.screen.show` selbst meldet, erscheint bei `/screen?action=status` als
anzeigefähig. Ein Gerät ohne TFT überspringt den Kartenweg dadurch vollständig
und gibt die Antwort ausschließlich per Sprache aus. `hide` wird entsprechend
nur an Geräte mit `self.screen.hide` geschickt.

## Neue Anzeige bauen

`templates/<name>.py` mit einer Funktion `render(data, size, load_font)`
anlegen; Farben und Grundflächen aus `palette.py` nehmen, damit alle Karten
zusammenpassen. Danach sofort benutzbar — die Firmware bleibt unverändert.

```python
def render(data, size, load_font):
    from PIL import ImageDraw
    import palette as P
    img = P.gradient(size)
    draw = ImageDraw.Draw(img)
    P.text(draw, (30, 30), data.get("titel", ""), load_font(40))
    return img
```

## Grenzen

- **Nur während eines Sprach-Turns.** Das Gerät hält die WebSocket-Sitzung nur,
  solange es zuhört oder spricht; im Standby meldet `/screen`
  `kein Geraet verbunden`. Die Anzeige muss deshalb aus dem Turn heraus
  ausgelöst werden (Skill `display-cards`).
- **Nur RGB565 für den Geräte-Push.** `--png` ist ausschließlich lokale
  Vorschau und wird bei einem echten Push ignoriert.
- **Speicher:** ein 480×480-Bild belegt 460.800 Byte im PSRAM. Für
  einzelne Karten unkritisch, für einen Dauerstrom nicht gedacht.
- **Kein Scrollen, keine Bedienung.** Ein Bild ist ein Bild; Knöpfe gibt es nur
  im A2UI-Weg der Room-Node-Firmware, nicht hier.

## Flashen

Die Firmware mit den beiden Werkzeugen entsteht über
`./scripts/build-firmware.sh --public-host <IP>` (Patch ist im Skript verankert).

**Nur die App-Partition schreiben**, nicht das Vollimage — sonst wird der
NVS-Bereich mit den WLAN-Zugangsdaten überschrieben und das Gerät ist offline,
bis es neu provisioniert wird:

```sh
./scripts/flash-firmware.sh --port /dev/ttyACM0 --board 4b --from-build
```

Vor jedem Eingriff ein Vollbackup ziehen:

```sh
esptool --port /dev/ttyACM0 read-flash 0x0 0x1000000 4b-$(date +%F).bin
```
