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

Eine Oberfläche für das, was sonst nur in JSON-Dateien steht: alle Geräte sehen,
Räume zuordnen, Sprechnamen vergeben, einen Satz testen. Gedacht als Gegenstück
zur Alexa-App — nur dass die Wahrheit in ioBroker bleibt und die Namen als
Overrides daneben liegen.

```bash
python panel.py            # http://0.0.0.0:8088
PANEL_PORT=9000 python panel.py
```

Dauerhaft: `systemd/geraeteverwaltung.service.in` (Platzhalter `@ALEXA_DIR@`,
`@BRIDGE_VENV@`, `@IOBROKER_URL@`, `@PANEL_PORT@`).

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

### Grenzen

- Ein Assistent gehört sinnvoll zu **einer** Gruppe. Trifft er auf mehrere,
  gewinnt die erste in der Liste.
- Nur Geräte mit **lokalem** Ziel (`localmap`) sind auswählbar. Reine
  Alexa-Kanäle lassen sich nicht gruppieren, weil sie über Amazon liefen.
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
