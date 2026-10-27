# Lokaler Smart-Home-Schnellweg

Dieser Quellbaum ist eine harte Laufzeitabhängigkeit von
`gateway/local-voice.mjs`. Eindeutige Schaltbefehle werden lokal aufgelöst und
über ioBroker Simple API ausgeführt, bevor ein Agentenlauf entsteht.

## Installation

`scripts/install-host.sh` kopiert die Quellen nach
`~/.local/share/alexa-direct/`. Der ioBroker-Endpunkt kann gesetzt werden:

```bash
export IOBROKER_SIMPLE_API=http://IOBROKER-IP:8087
```

Ohne Variable gilt der Referenzwert `http://192.168.178.24:8087`. Die Dateien
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
