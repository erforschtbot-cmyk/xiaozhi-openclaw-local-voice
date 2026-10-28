# Anzeigen auf dem Touch-LCD-4B

Kurzanleitung für die selbstgebaute Kartenanzeige. Nichts davon wird auf dem
ESP32 gespeichert — das Gerät holt sich nur das fertige Bild über HTTP.

## Wie es zusammenhängt

```
Vorlage (Python)  ->  render.py  ->  RGB565 .raw (480x480, 460800 Byte)
                                        |
                                        v
                            jarvis-screen.service  (HTTP :8770)
                                        |
                            show.py  ---+---> Bridge :8766 /screen
                                                      |
                                                      v
                                          self.screen.show  ->  Display
```

Das Gerät hat kein HTML, keinen Browser und keine Skriptsprache. Es bekommt
fertig dekodierte RGB565-Pixel und zeigt sie an. PNG wird nur als lokale
Vorschau erzeugt; auf dem 4B blieb der komprimierte PNG-Zeichenpfad schwarz.

## Werkzeuge

| Datei | Zweck |
|---|---|
| `render.py` | rendert `templates/<name>.py` zu RGB565 und optional zu PNG |
| `serve.py` | liefert `.raw`/Vorschauen über HTTP aus (systemd: `jarvis-screen`) |
| `show.py` | rendern + aufs Gerät schieben (der Alltagsbefehl) |
| `templates/*.py` | je Anzeige eine Datei |

## Eine Karte anzeigen

```sh
cd ~/.local/share/jarvis-screen
./show.py wetter --data '{"titel":"Morgen","ort":"Arnsberg","temp":18,
  "lage":"leichter Regen","spanne":"6–19 Grad","symbol":"regen",
  "zeilen":[["Vormittag","12","bewölkt"],["Nachmittag","19","Schauer"]]}'
```

Nur rendern, nicht senden: `--no-push`.
Vollbild wieder wegnehmen: `./show.py --hide`.
Erst prüfen, ob Anzeigen gerade geht: `./show.py --status`
(`{"can_show": true|false}` — bei `false` ist das Gerät im Standby und hält
keine Sitzung; Exit-Code 1).
Fertiges Bild senden: `./show.py --file /pfad/bild.png`.

## Eine neue Anzeige bauen

Datei `templates/<name>.py` anlegen:

```python
def render(data, size, load_font):
    img = ...          # PIL.Image in size x size
    return img
```

`data` ist das JSON von `--data`, `load_font(px)` liefert eine Schrift.
Farben und Grundflächen kommen aus `palette.py`, damit alle Karten
zusammengehören. Danach sofort nutzbar: `./show.py <name> --data '{...}'`.

## Was auf dem Gerät dafür geändert wurde

Firmware-Patch `firmware/patches/jarvis-screen-show.patch` ergänzt zwei
Werkzeuge:

- `self.screen.show` — lädt RGB565 in den PSRAM und zeigt es **vollflächig**
- `self.screen.hide` — zurück zum Gesicht

Ursprünglich vorhanden war nur `self.screen.preview_image`: 5 Sekunden,
70 %×50 % der Fläche, und für den Agenten unsichtbar (`user_only`). Damit war
sprachgesteuertes Anzeigen nicht möglich.

`self.screen.show` ist bewusst **kein** `user_only` — nur so kann der Agent es
auf Zuruf aufrufen.

## Grenzen

- Der verlässliche Weg ist **RGB565 little endian**, 480×480 = 460.800 Byte.
- Bild wird bei jedem Aufruf komplett in den PSRAM geladen. Für einzelne
  Karten unkritisch; ein Dauer-Stream wäre es nicht.
- Der Anzeigen-Server muss vom Gerät erreichbar sein — er lauscht auf allen
  Adressen. Die Bridge schreibt die Hostangabe automatisch auf ihrejenige
  LAN-Adresse um, über die das jeweilige Gerät verbunden ist.

## Bildsuche (Brave Images)

Ein echtes Foto statt einer gemalten Karte:

```sh
./image-search.py "Elefant Savanne"          # suchen und anzeigen
./image-search.py "Elefant" --list           # nur Treffer zeigen
./image-search.py "Elefant" --index 2        # dritter Treffer
```

Ablauf: Brave-Images-Abfrage (`brave.apiKey` aus `~/.openclaw/secrets.json`)
→ Original-URL bevorzugt, Thumbnail nur als Rückfall → Bild vollständig auf
480×480 einpassen → RGB565 → über `show.py` aufs Gerät.

Gemessen am 28.10.2026: **0,86–1,0 s** von Suchbegriff bis fertiger `.raw`.
Zum Vergleich: ein Bildmodell braucht 2–10 s, eine gerenderte Karte Millisekunden.

**Kein Zuschnitt.** Bei Querformat-Fotos auf quadratischem Display bleibt ein
Rand oben und unten (Balken in einem dunklen Ton aus dem Bild). Ein
quadratischer Beschnitt war zweimal schlechter: center-crop schnitt den
Elefanten an, und ein Versuch mit Kantenenergie wählte den Baum statt des
Motivs.

**Lizenz:** Brave liefert gemischt Getty/iStock (nicht frei) und
Pixabay/Unsplash/Pexels (frei). Trefferliste mit `--list` zeigt die Quelle.
Für private Anzeige unkritisch; bei Weitergabe freie Quellen wählen.
