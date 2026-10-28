---
name: display-cards
description: "Karte auf dem ESP32-Display des 4B anzeigen (Wetter, Listen, Status, beliebige Antworten). Auslöser: 'zeig mir das Wetter', 'schreib das auf den Bildschirm', 'zeig eine Liste', oder wenn eine Antwort besser als Bild als gesprochen passt."
---

# Anzeigen auf dem 4B-Display

Das Gerät hat kein HTML und keinen Browser. Es holt sich fertig gerenderte
**RGB565-Rohdaten** über HTTP und zeigt sie vollflächig an. PNG dient nur der
Host-Vorschau; auf dem 4B ist `.raw` der verlässliche Anzeigepfad.

## Der eine Befehl

```sh
cd /home/openclaw/.local/share/jarvis-screen
./show.py <vorlage> --data '<json>'
```

Beim Geräte-Push **niemals `--png` verwenden**. PNG ist nur mit
`--png --no-push` zur lokalen Vorschau gedacht; das Display erhält RGB565.

Vorlagen: `wetter` (Vorhersage) und `karte` (beliebiger Text/Liste).

## Beispiele

Wetter morgen (Daten vorher per Websuche holen):

```sh
./show.py wetter --data '{"titel":"Morgen","ort":"Arnsberg","temp":18,
 "lage":"leichter Regen","spanne":"6–19 °C","symbol":"regen",
 "zeilen":[["Vormittag","12","bewölkt"],["Nachmittag","19","Schauer"],
           ["Abend","11","klar"]]}'
```

Beliebige Liste oder Statusmeldung:

```sh
./show.py karte --data '{"titel":"Einkaufsliste","akzent":"gruen",
 "zeilen":[["Milch","2 l"],["Brot","1"]],"fuss":"Samstag"}'
```

Bild wieder weg: `./show.py --hide`.

## Echtes Foto zeigen (Bildsuche)

Wenn der Nutzer ein **Bild** sehen will statt einer Karte — „zeig mir einen
Elefanten", „zeig ein Foto von …", „wie sieht … aus" — dann nicht malen, sondern
suchen:

```sh
cd /home/openclaw/.local/share/jarvis-screen
./image-search.py "Elefant Savanne"
```

Auch fremde Dateien gehen: `./show.py --file /pfad/bild.jpg` passt das gesamte
Bild ohne Beschnitt auf 480×480 ein und wandelt es nach RGB565.

Dauer: **etwa eine Sekunde**. Das ist schnell genug, um es im Turn abzuwarten;
trotzdem kurz „Moment" sagen, weil das Gerät in der Zeit nichts anzeigt.

Treffer prüfen, wenn das Motiv nicht passt — dann anderen Treffer nehmen:

```sh
./image-search.py "Elefant" --list        # Titel und Quelle anzeigen
./image-search.py "Elefant" --index 2     # dritter Treffer
```

**Lizenz:** Brave mischt Getty/iStock (nicht frei) mit Pixabay/Unsplash/Pexels
(frei). Für die Anzeige im Wohnzimmer unkritisch. Sobald das Bild
weitergegeben oder veröffentlicht werden soll, freie Quelle wählen — `--list`
zeigt sie an.

Querformat-Fotos bekommen oben und unten einen Rand, weil das Display
quadratisch ist. Das ist Absicht: kein Beschnitt, also kein abgeschnittenes
Motiv.

## Regeln

- **Zuerst prüfen:** `show.py --status` → `{"can_show": true|false}`. Bei
  `false` ist Anzeigen gerade nicht möglich (Gerät im Standby); dann ehrlich
  sagen statt es still zu schlucken.
- **Nur während eines Sprach-Turns möglich.** Das Gerät hält die Verbindung
  nur, solange es zuhört oder spricht. Danach ist `/screen` nicht erreichbar
  (`kein Geraet verbunden`).
- Die Anzeige bleibt während der Antwort sichtbar. Sobald das Gerät wieder
  **Bereit** anzeigt, räumt die Firmware sie automatisch weg und zeigt den
  Smiley. `--hide` ist nur für ein vorzeitiges Ausblenden nötig.
- Vor dem Aufruf **nicht** lange vorlesen. Kurz sprechen („Moment, ich zeige es
  dir"), dann anzeigen. Die gesprochene Antwort darf kürzer sein, weil die
  Details auf dem Display stehen.
- Zahlen und Text müssen stimmen: lieber weniger Zeilen als erfundene.
- Neue Vorlagen sind eine neue Datei in `templates/`; die Firmware bleibt
  unverändert. Bau aber keine neue Vorlage, wenn `karte` reicht.

## Wenn etwas schiefgeht

- **Erfolg nur an `{"shown": true, ...}` erkennen.** Der gedruckte `Bild:`-Pfad
  ist nicht die ausgelieferte Datei und belegt nichts. Kommt kein `shown: true`,
  sag ehrlich, dass die Anzeige nicht geklappt hat.
- **HTTP 502 / `Downloaded image is invalid or unsupported`**: zuerst die
  ausgelieferte Datei prüfen, nicht das Format wechseln.
  `curl -s -o /dev/null -w '%{http_code} %{size_download}\n' http://127.0.0.1:8770/img/<vorlage>-480x480.raw`
  muss `200 460800` liefern. Weicht die Größe ab, ist die Datei im
  Auslieferungsordner veraltet: mit `--no-push` neu rendern, dann erneut senden.
  Ein 502 ist **nie** ein Grund, auf `--png` auszuweichen.
- **Wetterdaten — immer für Buchholz (Aller)**, nicht für den Host-Standort:
  `curl -s --max-time 20 'https://wttr.in/Buchholz%20%28Aller%29?format=j1' -o /tmp/w.json`
  Ohne Ortsangabe liefert wttr.in **Hannover** — das ist 80 km entfernt und
  falsch. Niemals nach dem Ort fragen: der Nutzer wohnt in Buchholz (Aller),
  Landkreis Heidekreis.
  **Die Postleitzahl 29690 nicht verwenden** — wttr.in löst sie nach
  Grand Le Crann in der Bretagne auf. Ebenso unbrauchbar: „Buchholz" allein
  (landet in NRW). „Buchholz (Aller)" löst nach Marklendorf (52.667, 9.717)
  auf, einem Ortsteil genau dieser Gemeinde.
  `weather[]` ist heute, morgen, übermorgen; „morgen" ist Index 1. Den Ort im
  Feld `ort` der Karte als „Buchholz" schreiben.
- **Eine Karte pro Auftrag.** Wiederholt der Nutzer denselben Wunsch, sendest du
  dieselbe Karte erneut — nicht mit anderen Werten neu rendern.

## Wenn etwas fehlt

- `kein Geraet verbunden` → Gerät hört gerade nicht zu, kein Fehler.
- Vorlage unbekannt → `ls /home/openclaw/.local/share/jarvis-screen/templates`.
- Anzeigen-Server prüfen: `systemctl --user status jarvis-screen`.
