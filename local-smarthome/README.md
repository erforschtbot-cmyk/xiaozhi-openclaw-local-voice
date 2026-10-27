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

## Zweite Erkennungsstufe: HassIL

Der eigene Resolver deckt **an/aus** und **Prozent** ab. Alles andere —
Farbe, Farbtemperatur, „maximum", „dimme auf 20" — landete bisher beim Agenten
und brauchte 7–12 s. Dafür gibt es jetzt eine zweite Stufe:
**HassIL**, der Intent-Parser von Home Assistant, mit fertigen deutschen
Satzvorlagen.

```text
Satz → eigener Resolver (erprobt)  → brauchbarer Wert? → schalten, fertig
                                   → sonst HassIL     → Wert? → schalten
                                                       → sonst → Agent
```

**Der eigene Weg behält Vorrang.** HassIL springt nur ein, wenn dort nichts
Brauchbares herauskommt — auch dann, wenn der Resolver zwar „Treffer" meldet,
aber keinen Wert hat (`action=percent, value=None` bei „auf maximum"). Genau
das war vorher der Fall: die Lampe wurde auf `null` gesetzt.

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
