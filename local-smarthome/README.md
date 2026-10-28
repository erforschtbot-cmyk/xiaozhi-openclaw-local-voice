# Lokaler Smart-Home-Schnellweg

Dieser Quellbaum ist eine harte Laufzeitabhängigkeit von
`gateway/local-voice.mjs`. Eindeutige Schaltbefehle werden lokal aufgelöst und
über ioBroker Simple API ausgeführt, bevor ein Agentenlauf entsteht.

## Installation

`scripts/install-host.sh` kopiert die Quellen nach
`~/.local/share/alexa-direct/`.

### ioBroker-Adresse eintragen

**Der wichtigste Punkt für einen Nachbau.** Früher stand die Adresse fest im
Quelltext (drei Dateien); wer das Projekt kopiert, hat eine andere IP und muss
sonst im Code suchen.

Am einfachsten im Panel: Feld **„ioBroker"** oben, Adresse eintragen, `setzen`.
Gilt sofort, ohne Neustart.

Alternativ ohne Oberfläche:

```bash
export IOBROKER_SIMPLE_API=http://IOBROKER-IP:8087
```

oder in `settings.json`:

```json
{ "iobroker_url": "http://IOBROKER-IP:8087" }
```

Reihenfolge (erste Quelle gewinnt):

| Rang | Quelle | gedacht für |
|---|---|---|
| 1 | `settings.json` | Eintrag in der Oberfläche |
| 2 | `IOBROKER_SIMPLE_API` | headless, Container, systemd |
| 3 | Referenzwert im Code | frischer Klon ohne Konfiguration |

Ohne jede Angabe gilt `http://192.168.178.24:8087` — der Wert aus dem Aufbau
dieses Hauses, also **nicht** für fremde Installationen geeignet. Die
Oberfläche zeigt an, woher der aktuelle Wert stammt, und markiert das Feld rot,
wenn die Adresse nicht erreichbar ist.

`settings.json` ist eigentümerspezifisch und deshalb **nicht** versioniert. Die Dateien
`catalog.json`, `localmap.json` und `rooms.json` werden aus ioBroker
erzeugt und sind deshalb nicht versioniert. `bridges.json` und
`rooms_overrides.json` sind dagegen gepflegte Konfiguration.

## Geräteverwaltung im Browser (`panel.py`)

Eine Oberfläche für das, was sonst nur in JSON-Dateien steht: Geräte sehen,
Sprechnamen vergeben, Räume zuordnen, Datenpunkte korrigieren, Gruppen bilden,
einen Satz testen. Gedanke: ein Gegenstück zur Alexa-App — mit dem Unterschied,
dass die Wahrheit in ioBroker bleibt und die Oberfläche nur Notizen danebenlegt.

### Aufrufen — welcher Port?

Die Seite läuft auf **Port 8088**, erreichbar aus dem ganzen LAN:

```text
http://<LAN-IP-des-Hosts>:8088
```

Beispiel für den Referenzhost: `http://192.168.178.143:8088/`

Den Port legst du selbst fest, in dieser Reihenfolge:

| Rang | Quelle | Beispiel |
|---|---|---|
| 1 | Umgebungsvariable `PANEL_PORT` | `PANEL_PORT=9000` |
| 2 | Standardwert im Code | `8088` |

Ebenso `PANEL_HOST` (Standard `0.0.0.0` = alle Netzwerkkarten). Für „nur dieser
Rechner" setz `PANEL_HOST=127.0.0.1`.

**Wenn du den Port nicht mehr weißt** — vier Wege, ihn zu finden:

```bash
# 1) Der Dienst nennt den Port beim Start (zuverlässigster Weg)
journalctl --user -u geraeteverwaltung.service -n 30 --no-pager | grep http
#   Geraeteverwaltung auf http://0.0.0.0:8088  (ioBroker: http://192.168.178.24:8087)

# 2) In der Unit nachsehen
grep PANEL_PORT ~/.config/systemd/user/geraeteverwaltung.service

# 3) Läuft der Dienst überhaupt?
systemctl --user status geraeteverwaltung.service

# 4) Offene Ports mit Prozess – der Name ist "python", nicht "panel"
ss -ltnp | grep python
#   LISTEN 0 5 0.0.0.0:8088 0.0.0.0:*  users:(("python",pid=2005822,fd=3))
```

**Hinweis:** `ss -ltnp | grep panel` findet nichts — der Prozess heißt `python`.
Suche nach dem Port oder nach `python`.

**Am einfachsten:** Die Seite zeigt ihren eigenen Port oben rechts in der
Kopfzeile, zusammen mit der Adresse, über die du sie gerade aufgerufen hast:

```text
02:31:12 · 6 Räume · Port 8088 (http://192.168.178.143:8088)
```

So siehst du sofort, ob du über `localhost` oder über die LAN-IP drin bist —
und welcher Port tatsächlich bedient wird, auch wenn du ihn per
`PANEL_PORT` geändert hast.

Der Startbefehl zeigt den Port ebenfalls direkt an:

```bash
python panel.py            # Geraeteverwaltung auf http://0.0.0.0:8088
PANEL_PORT=9000 python panel.py
```

Dauerhaft: `systemd/geraeteverwaltung.service.in` (Platzhalter `@ALEXA_DIR@`,
`@BRIDGE_VENV@`, `@IOBROKER_URL@`, `@PANEL_PORT@`). Die auf dem Referenzhost
aktive Fassung steht in `systemd/geraeteverwaltung.service.reference`.

### Wie es technisch funktioniert

Kein Framework, keine zusätzliche Abhängigkeit:

```text
Browser  ──HTTP──►  panel.py  (http.server aus der Standardbibliothek)
                       │
                       ├─ liest:  localmap.json, catalog.json, rooms.json
                       │          names_overrides.json, groups.json, settings.json
                       ├─ fragt:  ioBroker Simple API (getBulk) für Live-Werte
                       └─ ruft:   control.handle(...) für den Satz-Test
```

Warum `http.server`: Der Host hat keine Web-Bibliothek (kein Flask, FastAPI,
aiohttp). Statt etwas nachzuinstallieren, das nur für eine Verwaltungsseite
gebraucht wird, nutzt `panel.py` die Standardbibliothek. Ein Thread je Anfrage
(`ThreadingHTTPServer`), damit ein langsamer ioBroker-Aufruf die Seite nicht
blockiert.

**Der Datenfluss beim Laden:**

```text
GET /api/devices
  1. localmap.load()     43 Geräte mit belegtem LOKALEM Ziel
  2. catalog.load()     124 Geräte aus dem Alexa-Katalog
  3. zusammenführen      lokale Ziele haben Vorrang (schalten ohne Amazon)
  4. Overrides anwenden  Namen, Räume, Datenpunkt-Vorgaben
  5. getBulk             einen einzigen Aufruf für alle Zustände (80er-Blöcke)
  6. sortieren           nach Raum, dann Name; Ausgeblendete ans Ende
```

Ein Aufruf statt 126 — das ist der Grund, warum die Seite in Millisekunden
lädt statt Sekunden.

**Die API-Endpunkte:**

| Methode | Pfad | Aufgabe |
|---|---|---|
| GET | `/` | die Seite selbst (HTML + CSS + JS in einem Dokument) |
| GET | `/api/devices` | alle Geräte, Räume, ioBroker-Adresse und deren Herkunft |
| GET | `/api/groups` | Gruppen, Sprachgeräte, wählbare Geräte |
| POST | `/api/rename` | Sprechname setzen (Einzelfall) |
| POST | `/api/room` | Raumzuordnung setzen (Einzelfall) |
| POST | `/api/change-target` | Datenpunkt-Vorgabe setzen (Alias: `/api/target`) |
| POST | `/api/save-batch` | **mehrere** Änderungen in einem Schreibvorgang |
| POST | `/api/delete` | Gerät ausblenden oder wieder einblenden |
| POST | `/api/groups` | Gruppenliste ersetzen |
| POST | `/api/settings` | ioBroker-Adresse setzen |
| POST | `/api/test` | Satz auflösen (`dry_run`, schaltet nichts) |

Die Oberfläche nutzt `save-batch` — deshalb ein Speichern-Knopf für alle
Änderungen statt eines Schreibvorgangs pro Feld.

### Speichern: erst auf Knopfdruck

Nichts wird beim Tippen geschrieben. Jedes Feld merkt sich seinen Ausgangswert
(`data-v`); weicht der Inhalt ab, wird das Feld gelb umrandet und die Zeile
hinterlegt. Der Speichern-Knopf zeigt die Zahl offener Zeilen und bleibt grau,
solange nichts geändert wurde. Ein Klick schickt **alle** Änderungen und lädt
die Liste neu.

Das ist bewusst so: ein abgebrochener Speichervorgang hinterlässt keinen halben
Stand, und du kannst mehrere Geräte in einem Zug ändern.

### Geräte aufklappen — ein Datenpunkt je Fähigkeit

Ein Gerät hat oft mehr als einen Datenpunkt. „Licht Arbeitszimmer" führt vier:

```text
onoff       zigbee.0.group_1.state        Ein/Aus
brightness  zigbee.0.group_1.brightness   Dimmen
color       zigbee.0.group_1.color        Farbe (JSON)
colortemp   zigbee.0.group_1.colortemp    Weißton (Kelvin)
```

Der Pfeil vor dem Sprechnamen klappt eine Detailzeile auf: eine Zeile je
Fähigkeit, jede mit eigenem Feld. Nur dort kannst du etwa den Farb-Punkt
korrigieren, ohne Ein/Aus zu verbiegen.

Die Rolle des Ziels entscheidet über das Format — und das ist keine Kleinigkeit:

| Rolle | Beispiel | erwartet |
|---|---|---|
| `level.color.rgb` | `zigbee.0.<id>.hex_color` | `#rrggbb` |
| `level.color` | `zigbee.0.group_1.color` | `{"r":..,"g":..,"b":..}` |
| `level.color.temperature` | `colortemp` | Kelvin (2200 … 7000) |
| `level.dimmer` | `brightness` | 0 … 100 |

Falsch geschrieben nimmt der Adapter den Wert trotzdem an — die Lampe reagiert
aber nicht. Genau das war der Fehler vom 28.10.2026: JSON auf `hex_color`.

### Gruppen

Siehe den Abschnitt [Gruppen](#gruppen-ein-gerät-schaltet-viele). Kurz: Eine
Gruppe gehört einem **Sprachgerät**, nicht einem Raumnamen. Standardmäßig sind
die Gruppen zugeklappt; der Zustand liegt im Browser (`localStorage`) und
überlebt ein Neuladen.

### Was die Oberfläche zeigt

| Spalte | Bedeutung |
|---|---|
| Sprechname | Name, der gesprochen werden muss — hier änderbar |
| Raum | Zuordnung; leer heißt „Ohne Raum" |
| Weg | `lokal` = ohne Amazon, `Alexa-Cloud` = über Amazon |
| Zustand | aktueller Wert aus ioBroker, live abgefragt |
| Datenpunkt | das genaue Ziel, das geschrieben wird |

**„kein Rückkanal"** bedeutet: Der Datenpunkt ist ein reiner Befehl
(`common.read: false`, z. B. `.active` bei Szenen). Es gibt keinen Zustand
zum Anzeigen — das ist kein Fehler, sondern Bauart des Adapters.

### Namen ändern

Die Namen liegen in `names_overrides.json` (nicht versioniert, weil
eigentümerspezifisch). `catalog.py` und `localmap.py` legen sie beim Laden über
die Rohdaten:

```
dev["name"]     = vergebener Name
dev["baseName"] = ursprünglicher Name aus ioBroker
```

Der Abgleich in `resolver.py` arbeitet auf `name`. Ein neuer Name wirkt deshalb
sofort im Sprachweg — **und der alte funktioniert danach nicht mehr**. Wer
„Monitor" in „Bildschirm" umbenennt, muss „Bildschirm" sagen. Ein leerer Name
löscht die Zuordnung und stellt den ursprünglichen Namen wieder her.

### Datenpunkt ändern

Die Spalte „Datenpunkt" ist ein Eingabefeld. Ein eingetragener Wert
überschreibt das automatisch ermittelte Ziel — gedacht für defekte Aliasse,
falsch erkannte Aktoren oder Geräte, die bisher nur über die Alexa-Cloud
liefen. Der `×`-Knopf entfernt die Vorgabe wieder.

Die Vorgabe wirkt **auch im Sprachweg**, nicht nur in der Anzeige:
`local_target()` und `execute()` prüfen sie vor dem abgeleiteten Ziel. Ob der
Weg danach `lokal` oder `Alexa-Cloud` heißt, richtet sich nach dem Ziel selbst
(`alexa2.`, `iot.` und `cloud.` bleiben Cloud) — sonst behauptet die Anzeige
einen Amazon-freien Weg, den es nicht gibt.

Gültige Ziele sind alle schreibbaren ioBroker-Datenpunkte, z. B.:

```text
zigbee.0.0017880103544b64.state          Schalter
zigbee.0.<id>.brightness                 Helligkeit
0_userdata.0.Raum.Geraet                 eigener Zustand
alexa2.0.Smart-Home-Devices.<id>.powerState   Alexa-Kanal
```

Bevor du speicherst: Der Datenpunkt muss existieren und `write: true` haben,
sonst schlägt das Schalten später fehl. Die Seite prüft das nicht.

### Gerät aus der Liste nehmen

Der Knopf „löschen" nimmt ein Gerät aus der Liste — mit Sicherheitsabfrage.
Es wird danach **nicht mehr geschaltet und nicht mehr erkannt**, bleibt aber
in ioBroker vollständig bestehen.

```text
löschen   → deleted-Liste in names_overrides.json, Zeile wird durchgestrichen
          → Sprachweg filtert es heraus ("kein-geraet")
          → Panel zeigt es weiter, am Ende der Liste, mit Knopf "einblenden"
einblenden → Eintrag wird entfernt, Gerät ist wieder schaltbar
```

**In ioBroker wird nichts gelöscht.** Das ist keine Vorsicht, sondern die
einzige Möglichkeit: Die Simple-API kennt keinen Löschbefehl — `/delete/…` und
`/delObject/…` antworten beide mit `command delete unknown` (geprüft
27.10.2026). Ein echtes Entfernen ginge nur über den ioBroker-Admin oder
`iobroker object del`.

Ausgeblendete Geräte erscheinen in der Statistik als „N ausgeblendet" und
tragen eine durchgestrichene Zeile.

### Satz testen

Der Knopf „Auflösen" schickt den Satz durch `control.handle(..., dry_run=True)`.
Es wird **nichts geschaltet** — die Antwort zeigt Gerät, Aktion, Weg und
Datenpunkt. Das ist der schnellste Weg, eine Mehrdeutigkeit zu finden, ohne sie
im Wohnzimmer zu hören.

### Wenn die Seite leer bleibt

Häufigster Fehler in diesem Projekt — er trat am 27. und 28.10.2026 zweimal auf.
Symptom: Kopfzeile, Filter und Knöpfe sind da, aber keine Geräteliste. Kein
Fehler in der Konsole des Servers, nur im Browser.

Ursache: Im Python-String steht ein einzelnes `\n`. Python macht daraus beim
Ausliefern einen **echten Zeilenumbruch mitten im JavaScript-String**, das
Skript ist zerrissen, `render()` läuft nie.

Prüfen statt raten:

```bash
python3 scripts/check-panel-js.py
```

Das Skript nimmt `panel.PAGE`, zieht den `<script>`-Block heraus und lässt
`node --check` darauf los — also genau den Text, den der Browser bekommt, nicht
die Quelldatei. Genau diese Verwechslung hatte den Fehler zweimal übersehen.

```text
Seite: 28655 Zeichen, JavaScript: 21815 Zeichen
  ausgelieferte JavaScript-Syntax: gültig

In Ordnung.
```

(Die Zahlen wachsen mit jeder Änderung an der Seite — vergleiche sie nicht,
sondern achte auf „gültig" und „In Ordnung".)

Bei kaputter Datei endet es mit Exit 1 und nennt die Stelle:

```text
  FEHLER: ausgelieferte JavaScript-Syntax ist kaputt
```

Nach jeder Änderung an `panel.py` laufen lassen — vor dem Neustart.

Zweiter häufiger Grund: Der Browser hält die alte Fassung. **Strg+F5**.

### Grenzen

- Die Seite schaltet nichts. Schalten passiert weiter über den Sprachweg.
- Live-Werte kommen aus `getBulk`; reine Befehlsdatenpunkte liefern dort nichts.
- Räume stammen aus `rooms.json` (abgeleitet) plus den Overrides. Ein Raum
  erscheint also nur, wenn er dort belegt ist.
- Ein geänderter Datenpunkt wird nicht auf Existenz geprüft. Ein Tippfehler
  fällt erst beim Schalten auf.
- „Löschen" heißt ausblenden. Das Gerät verschwindet aus Liste und Sprachweg,
  bleibt in ioBroker aber unverändert.

## Gruppen: ein Gerät schaltet viele

Auf dem Gerät im Wohnzimmer soll **„Licht an"** genügen — ohne Raumnamen. Dafür
gibt es Gruppen.

Eine Gruppe hat drei Felder:

| Feld | Bedeutung |
|---|---|
| Name | **nur eine Bezeichnung** — ohne Wirkung aufs Schalten |
| Sprachassistent | Device-ID des Geräts, das das Wachwort hört (**Pflicht**) |
| Geräte | was geschaltet wird, sobald dieser Assistent „Licht …" hört |

**Entscheidend ist der Assistent, nicht der Gruppenname.** Der Sprachweg
bekommt die Device-ID vom Server (`OPENCLAW_VOICE_DEVICE_ID`, aus
`room-map.json` des Bridges) und schlägt darüber die Gruppe nach.

### Was greift

```text
"Licht an"         auf dem Wohnzimmer-Gerät  → Gruppe: alle Geräte der Gruppe
"Licht blau"       auf dem Wohnzimmer-Gerät  → Gruppe: Farbe auf allen
"Licht auf 50 %"   auf dem Wohnzimmer-Gerät  → Gruppe: Helligkeit auf allen
"Licht an"         auf einem Gerät ohne Gruppe → wie bisher (kein-geraet)
"Licht Arbeitszimmer an"  → Gerät, nicht Gruppe
"Wohnzimmer an"           → room_batch, nicht Gruppe
```

Die Reihenfolge ist wichtig: `group_batch` greift **nur**, wenn im Satz weder
ein Gerätename noch ein Raum steht. Beides wird gegen zwei Quellen geprüft —
den eigenen Resolver (43 Geräte) und HassIL (118) —, weil jede für sich
unvollständig ist.

### Anlegen

Im Panel unter **Gruppen** → „+ Gruppe anlegen": Namen vergeben, Assistenten
anhaken (Pflicht), Geräte anhaken, „Gruppen speichern".

Gespeichert wird in `groups.json` — eigentümerspezifisch, nicht versioniert.

Ohne angehakten Assistenten wirkt eine Gruppe **nicht**; das Panel fragt beim
Speichern nach.

### Alle Geräte sind wählbar — mit Unterschied

In der Gruppenauswahl stehen **alle** schaltbaren Geräte: 43 mit lokalem Ziel
(`zigbee.0.*`, `wled.0.*`, `sonoff.0.*`, `0_userdata.0.*`) und 70 mit reinem
Alexa-Kanal (`alexa2.0.Smart-Home-Devices.*`). Cloud-Geräte tragen in der
Auswahl die Marke **Cloud**.

Der Unterschied liegt nicht beim Schreiben, sondern danach:

| | lokal (`zigbee.0.*` …) | Cloud (`alexa2.*`) |
|---|---|---|
| Befehl geschrieben | lokal nach ioBroker | lokal nach ioBroker |
| Ausführung | direkt am Funkmodul | alexa2 → Amazon → Gerät |
| Rückkanal | ja, Wert wird zurückgelesen | nein (`readable: false`) |
| Bestätigung | belastbar | nur „Befehl angenommen" |
| Dauer | ~20 ms | spürbar länger |

Kurz: Ein Alexa-Kanal wird **lokal angesprochen**, aber über die Cloud
ausgeführt. Deshalb ist die Bestätigung schwächer — die Gruppe meldet dann
„geschaltet", ohne den Zustand prüfen zu können.

Lokale Ziele haben Vorrang, wenn ein Gerät in beiden Quellen steht.

### Grenzen

- Ein Assistent gehört sinnvoll zu **einer** Gruppe. Trifft er auf mehrere,
  gewinnt die erste in der Liste.
- Bei Cloud-Geräten bleibt die Bestätigung schwach (siehe oben).
- „heller"/„dunkler" funktioniert auch hier nicht — relativ ohne Istwert.

## Erkennung: HassIL zuerst

**HassIL** ist der Intent-Parser von Home Assistant, mit fertigen deutschen
Satzvorlagen. Er ist die **erste** Stufe; der eigene Resolver ist der
Rückfall.

```text
Satz → room_batch      (<Raum> an/aus → alle Lampen des Raums)
     → HassIL          (Farbe, Farbtemperatur, maximum, „dimme auf 20", …)
     → eigener Resolver (was HassIL nicht kennt)
     → Agent
```

### Warum der eigene Resolver bleibt

„Komplett auf HassIL" hieße, ihn zu entfernen. Das wurde gemessen, nicht
geschätzt — an den 34 Regressionstests:

| | HassIL allein | mit eigenem Resolver |
|---|---|---|
| schaltbare Fälle | 18 von 25 | **25 von 25** |
| fälschlich geschaltet | 0 von 9 | 0 von 9 |

Diese sieben gingen ohne ihn verloren, weil HassIL dort **nichts** findet:

```text
"mach die jalousie runter"        → device_class blind, kein Gerät
"schalte alle lampen an"          → nur domain, kein Gerät
"starte den kino pc"              → „Kino Arbeitszimmer" statt „KinoPC"
"stell die lampe im schlafzimmer auf 20 prozent"
"schalte die steckdose am sofa ab"
"dreh die deckenstrahler links runter"
"stell die temperatur klimaanlage auf 22 grad"
```

Sie landen jetzt beim eigenen Resolver statt beim Agenten (7–12 s). Der
Vergleich alt/neu über alle 34 Fälle: **gleicher Datenpunkt, gleicher Wert,
gleiche Ablehnungen** — HassIL entscheidet 16 davon, der Resolver 18.

### Zwei Schutzregeln, die nötig waren

Beides beim Umstieg gefunden (27.10.2026):

**`floor` wird verworfen.** Es gibt hier keine Etagen. Trifft HassIL darauf,
hat es den Namen nicht verstanden, sondern Reste als Etage gedeutet:
`"monitorlich an"` → `name="Monitor", floor="lich"` — geschaltet wurde der
Monitor statt des Monitorlichts.

**Name und Raum müssen zusammenpassen.** `"heizung badezimmer an"` ergab
`name="Heizung"` (gehört zum Arbeitszimmer) und `area="Badezimmer"`. Ohne
Prüfung wurde die Heizung im **falschen Raum** geschaltet. Jetzt gewinnt das
Gerät, dessen Name beide Teile enthält (`Badezimmer Heizung`); passt nichts
eindeutig, wird abgelehnt statt geraten.

### Was jetzt lokal geht

| Befehl | Ziel |
|---|---|
| „Licht Arbeitszimmer blau" | `group_1.color` = `{"r":0,"g":0,"b":255}` |
| „Licht Arbeitszimmer auf warmweiß" | `group_1.colortemp` = 370 (2700 K als Mired) |
| „Licht Arbeitszimmer auf maximum" | `group_1.brightness` = 100 |
| „dimme das Licht in der Küche auf 20" | `…brightness` = 20 |
| „Monitorlicht auf 50 prozent" | `…brightness` = 50 |

Gemessen: 14 von 15 geprüften Sätzen werden schaltbar.

### Grenzen, ehrlich

- **„heller"/„dunkler" bleibt offen.** HassIL kennt ebenfalls keine relativen
  Angaben ohne Istwert. Das ist ein eigener Punkt.
- **Farbtemperatur wird nach Mired umgerechnet** (1000000 / Kelvin), weil
  zigbee das so führt. 2700 K → 370. Stimmt für die vorhandenen Lampen,
  ist aber eine Annahme, die an einer Lampe geprüft werden sollte.
- **Räume lösen auf ein Gerät auf.** Nennt ein Raum mehrere Lampen
  (Wohnzimmer: 5), nimmt die Stufe die erste passende — nicht alle. Für
  „alle Lichter im Wohnzimmer" bleibt der Raum-Gruppen-Weg des eigenen
  Resolvers zuständig, und der greift vorher.

### Herkunft und Lizenz

Die Vorlagen unter `hassil-data/` sind **unveränderte Kopien** aus
[OHF-Voice/intents](https://github.com/OHF-Voice/intents) (CC-BY 4.0).
Der Parser [hassil](https://github.com/OHF-Voice/hassil) ist Apache-2.0.
Beides verlangt Nennung — sie steht dauerhaft in
[`NOTICE.md`](NOTICE.md). Die YAML-Dateien selbst werden **nicht** angepasst;
alles Haus-spezifische steckt in `hassil_router.py`.

### Zurücksetzen

Die Integration ist vollständig rückbaubar:

```bash
cd ~/.local/share/alexa-direct
./restore-vor-hassil.sh            # zeigt nur, was passieren würde
./restore-vor-hassil.sh --apply    # stellt wirklich zurück
systemctl --user restart geraeteverwaltung.service
systemctl --user restart jarvis-realtime-bridge.service
```

Zurückgesetzt wird `control.py` aus dem Backup
(`alexa-direct-backup-vor-hassil-*`), entfernt werden `hassil_router.py`,
`NOTICE.md` und `hassil-data/`. Namen, Räume und Kataloge bleiben unberührt.

## Erstaufbau und Prüfung

```bash
cd ~/.local/share/alexa-direct
python catalog.py
python localmap.py
python rooms.py
python test_resolver.py
SMARTHOME_DRY_RUN=1 python smarthome_cli.py "Monitor an"
```

Der letzte Befehl darf kein Gerät schalten. Für einen anderen Haushalt müssen
die beiden gepflegten JSON-Dateien angepasst werden; unbekannte oder mehrdeutige
Sätze fallen sicher auf den normalen Agentenweg zurück.

Mit `touch ~/.local/share/jarvis-realtime-bridge/disable-local-smarthome` wird
dieser Schnellweg vollständig deaktiviert.
