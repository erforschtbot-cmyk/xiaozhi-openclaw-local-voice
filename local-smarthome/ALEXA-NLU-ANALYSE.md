# Alexa-Geräte direkt schalten (ohne Alexa-NLU)

Stand: 2026-10-26. Gebaut für Elzershark, ioBroker auf 192.168.178.24.

## Zweck

Satz rein, Gerät schaltet — ohne dass der Text als `textCommand` an einen Echo
geht und dort erst von Amazons Spracherkennung/NLU interpretiert werden muss.
Der Weg ist:

    Satz -> resolver.py (lokal) -> control.py -> simple-api (8087)
         -> alexa2.0.Smart-Home-Devices.<id>.powerState

## WICHTIG: Das ist NICHT lokal

`powerState` ist kein lokaler Funkweg. Der Adapter ruft über `alexa-remote2`
`executeSmarthomeDeviceAction()` auf, und die Bibliothek macht
`PUT /api/phoenix/state` gegen **Amazons Cloud** (belegt in
`alexa-remote2@8.1.1`, `alexa-remote.js:3627`).

Was der Weg also spart: den Echo-Umweg und die Sprach-NLU. Was er NICHT spart:
Amazon. Fällt das Internet oder der Alexa-Login aus, schaltet nichts.

Wer echte lokale Steuerung will, muss den darunterliegenden Datenpunkt nehmen
(zigbee/shelly/tuya/wled) — siehe Punkt 3 unten.

## Drossel erklärt sich damit

Die 4 Schreibvorgänge pro 10 Minuten (Punkt 2) sind sehr wahrscheinlich der
Grund, warum der Adapter so knapp begrenzt: jede Schaltung ist ein Cloud-Aufruf
an Amazons Phoenix-API. Das ist kein Adapter-Fehler, sondern Schutz vor
Rate-Limiting durch Amazon.

## Dateien

| Datei | Aufgabe |
|---|---|
| `catalog.py` | baut `catalog.json` aus den echten Objekten (124 schaltbare Geräte) |
| `resolver.py` | deutscher Parser: Gerätename, Aktion, Wert |
| `control.py` | schreibt den Datenpunkt, liest ack zurück |
| `test_resolver.py` | 34 Offline-Fälle gegen den echten Katalog |
| `/home/openclaw/.local/bin/alexa-direct` | CLI-Wrapper |

## Messwerte (2026-10-26, Testlampe = virtuelles Gerät)

- HTTP-Annahme durch ioBroker: 7–30 ms
- ack (Adapter hat übernommen): 0,44 s / 0,64 s für an/aus
- Physische Wirkung: **nicht** verifiziert, nie an einem echten Gerät getestet

## Wichtige Befunde

1. **`getObjects` liefert die Kanal-Objekte (Gerätenamen) auf diesem ioBroker
   nicht mit.** Namen müssen einzeln über `/get/<id>` gelesen werden, sonst
   entsteht ein leerer Katalog.
2. **Der Adapter drosselt.** `main.js:1655 checkSmartHomeControlParameters`:
   max. 4 akzeptierte Schreibvorgänge pro Gerät in 10 Minuten, zusätzlich wird
   derselbe Zahlenwert innerhalb von 5 min verworfen. Überschreitung steht als
   `Ignore update ... set too often (N)` im Log und liefert **trotzdem HTTP 200**.
   HTTP 200 ist deshalb kein Erfolgsbeweis.
3. **68 der 124 schaltbaren Geräte sind `ioBroker Group`** — virtuelle Geräte,
   die ioBroker selbst per iot-Adapter zu Alexa publiziert. Dort ist der Weg
   über alexa2 ein Umweg durch Amazon; der lokale Weg wäre der darunterliegende
   zigbee/shelly/tuya/wled-Datenpunkt (202 vorhanden).
4. **Kein fertiges Projekt gefunden.** alexa-cli, openclaw-alexa-voice,
   AlexaClaw-Client lösen andere Richtungen; Matter-Adapter ist installiert,
   aber disabled und nur für Matter-Geräte.
5. Der bestehende Jarvis-Fastpath (`disable-smart-home-fastpath`) schickt den
   Satz an `Commands.textCommand`, also **durch** Amazons NLU. Das ist der
   langsame Weg.

## Verhalten bei Unsicherheit

`resolve()` gibt `ok=False` mit Grund und Kandidatenliste zurück, statt zu raten:
`frage`, `verneint`, `mehrere-aktionen`, `kein-geraet`, `zu-unsicher`,
`mehrdeutig`, `faehigkeit-fehlt`. Ein Gerät, dem die Fähigkeit fehlt, wird
**nicht** durch ein anderes ersetzt.

## Offen

- Live-Test an einem echten Gerät (Freigabe nötig)
- Einhängen in den Sprachpfad (Jarvis) — Freigabe nötig
- Mapping der 68 virtuellen Gruppen auf lokale Datenpunkte

## Vergleich mit dem bestehenden Jarvis-Router (gemessen)

`~/.local/share/jarvis-realtime-bridge/smart-home-router.mjs` existiert seit
19.10. und ist die *langsamere* Variante: er sendet den Satz an
`alexa2.0.Echo-Devices.<sn>.Commands.textCommand`, also durch Amazons NLU.
Katalog: 22 Einträge, bewusst konservativ (nur `powerState`, normale Lasten,
keine Fuzzy-Treffer).

Gegen die 18 aussagekräftigen Fälle aus `test_resolver.py`:

| | Treffer |
|---|---|
| bestehender Router (textCommand) | 7/18 |
| neuer Resolver (powerState direkt) | 18/18 |

Der bestehende Router ist nicht schlecht — er ist absichtlich vorsichtig und
schickt unbekannte Sätze an den KI-Agenten weiter. Der neue Resolver löst mehr
Fälle selbst auf, kostet dafür aber einen Cloud-Aufruf pro Schaltung.

## Empfehlung

Nicht den einen gegen den anderen tauschen, sondern staffeln:

1. **Lokale Geräte** (zigbee/shelly/tuya/wled, 202 Datenpunkte): direkt lokal
   schalten. Schnellste und einzige internetunabhängige Variante.
2. **Virtuelle ioBroker-Gruppen** (68 Stück): über ihre Mitglieder lokal
   auflösen, statt durch Amazon zu gehen.
3. **Rest, der nur über Alexa existiert** (Govee, LG, Roborock, Blink …):
   `powerState` über alexa2 — Cloud, aber ohne Echo-Umweg.
4. Unbekanntes weiterhin an den KI-Agenten.

## Lokales Routing (Stand 2026-10-26, nach iot-Abgleich)

Quelle der Zuordnung ist **nicht** geraten, sondern gelesen:
`common.smartName.de` (Name in der Alexa-App) und `common.alias.id`
(echter lokaler Datenpunkt) aus dem iot-/alias-Adapter.

- 124 schaltbare Alexa-Geräte, davon **42 mit belegtem lokalem Ziel**
- **65 einzelne Ziele** per API geprüft: alle vorhanden und `write=true`
- Aufruf: `alexa-direct "…" --local` bevorzugt das lokale Ziel
- Fehlt ein `ON`-Alias, wird Ein/Aus aus dem Schwester-Alias abgeleitet
  (gleiche Zigbee-Geräte-ID, nur bei genau einem Kandidaten mit
  `switch`-Rolle und `write=true`)

Trockenlauf über alle 42: **33 routen lokal**, 9 nicht — und zwar aus
nachvollziehbaren Gründen:

| Gerät | Warum nicht lokal |
|---|---|
| Ladezeit, Lampe Küche, Lampe Schlafzimmer | haben kein `onoff`-Alias, nur Dimmen/Farbe → Ein/Aus bleibt Cloud |
| Lüfter, Temperatur Klimaanlage | nur `setpoint`, kein Ein/Aus |
| Heizung, Stunde, Uhr, Licht Arbeitszimmer | **mehrdeutig** (doppelte Alexa-Namen) → bewusst kein Routing |

Das lokale Routing matcht **nur exakt** auf den smartName, nie fuzzy — sonst
könnte ein ähnlicher Name das falsche Gerät schalten.

Details und offene Fragen: `UNSTIMMIGKEITEN.md`.

## Änderungen an der ioBroker-Konfiguration (2026-10-26)

Auf Freigabe des Eigentümers, mit Backup in `backup-alias/`:

| Objekt | Änderung | Ziel |
|---|---|---|
| `alias.0.Küche.Lampe_Küche.ON` | **neu angelegt** | `zigbee.0.0017880103544b64.state` |
| `alias.0.Schlafzimmer.Lampe_Schlafzimmer.ON` | Ziel umgebogen, `role` ergänzt | `zigbee.0.a4c138ae1ecf2c06.state` |

Beide live über den Alias getestet (an/aus, je 10 ms), Ausgangszustand danach
wiederhergestellt (beide Lampen aus).

Hinweis: `alias.0` wird vom Adapter `devices.0` („Geräte") verwaltet. Per API
angelegte Objekte erscheinen unter `alias.0`, aber nicht zwingend in der
Geräte-Ansicht. Ursache für die Abweichung: 7 Objekte unter `alias.0` haben
keine `role` und stammen von `simple-api` statt vom Adapter.

## Die 3 Restfälle: kein lokales Ein/Aus vorhanden

Geprüft am 2026-10-26 über alle Objekte mit passendem Namensmuster — es gibt
für keinen der drei einen schaltbaren Ein/Aus-Punkt, auch nicht unter anderem
Namen. Sie bleiben deshalb bewusst auf dem Cloud-Weg.

| Gerät | lokal vorhanden | Ein/Aus? |
|---|---|---|
| `Uhr` (Echo Wall Clock) | `dimmer`, `hue`, `saturation`, `temperature` | nein — nur Dimmen/Farbe |
| `Ladezeit` (Tibber) | 24 Werte, alle `role=state/value/indicator` | nein — reine Anzeige-/Steuerwerte |
| `Stunde` (Tibber-Preis) | nur `value.temperature` | nein — Preisfenster, kein Gerät |

Ein Ein/Aus ließe sich nur durch neue Datenpunkte herstellen. Das wäre keine
Reparatur, sondern eine Erweiterung — bewusst nicht gemacht.

## Ortsnamen sind immer Licht (2026-10-27)

„Schlafzimmer an" meint die Lampe, nicht die Klimaanlage. Umsetzung:

- `NOT_LIGHT` schließt Klima, Heizung, Ventilator, Steckdose, Rollo, Kamera …
  aus der Raumauflösung aus.
- Lampe ist, was Dimmen/Farbe kann oder „Licht/Lampe/Beleuchtung/Strahler/
  Ambilight/LED/Regenbogen/Feuer" im Namen trägt.
- Gibt es genau **eine** Lampe im Raum, gewinnt sie gegen Schalter.
  Gibt es mehrere, wird gefragt statt geraten.

Getestet: schlafzimmer/wohnzimmer/arbeitszimmer/küche/badezimmer/
wegbeleuchtung/terrasse → alle lokal, alle die richtige Lampe.

## Brücken: 0-100-Wert → Schalter (Terrasse)

`Meine_Geräte.0.Terrasse_Regebogen` ist ein Prozentwert (0–100). Das aktive
Skript „Terrasse Regenbogen" macht daraus `wled.0.84f3ebfb5bf9.on`:
Wert ≠ 0 → an, Wert = 0 → aus.

`bridges.py` leitet solche Brücken aus den Skripten ab und schreibt
`bridges.json`. Übernommen wird nur, wenn ein aktives Skript **genau einen**
Schalter setzt. Aktuell existiert genau eine solche Brücke (Terrasse).

Live getestet: `wled.0.84f3ebfb5bf9.on` an/aus in 20 ms, Ausgangszustand
wiederhergestellt.

## Räume mit mehreren Lampen (2026-10-27)

Ein Ortsname schaltet **alle Lampen des Raums**. Die Zuordnung stammt aus zwei
belegten Quellen (`rooms.py` → `rooms.json`):

1. **Bewegungs-Skripte**: ein aktives Skript, das auf einen Bewegungsmelder
   hört und mehrere Lichter schaltet, beschreibt genau die Lampen seines Raums.
   Das ist die einzige Quelle, die auch Lampen **ohne** alias erfasst.
2. **alias-Pfade**: `alias.0.<Raum>.<Gerät>.<Mitglied>`, nur die Ein/Aus-Punkte
   (`.state`, `.on`, `.power`), Steckdosen/Klima/Rollo ausgeschlossen.

Stand: **6 Räume**. Wohnzimmer = 5 Lampen:

| Lampe | Datenpunkt | Quelle |
|---|---|---|
| WLED | `wled.0.4c11ae10889f.on` | Skript |
| Yeelight-Decke | `yeelight-2.0.ceiling1-…control.power` | Skript |
| 5-m-Zigbee-Streifen | `zigbee.0.a4c138e3e6d1f4cd.state` | alias |
| Deckenstrahler links | `zigbee.0.a4c13834b389ea43.state` | alias |
| Deckenstrahler rechts | `zigbee.0.a4c138364a76c01a.state` | alias |

Live getestet: „wohnzimmer an" schaltet alle 5 in **43 ms**, Ausgangszustand
danach wiederhergestellt (alle false).

### Bewegungsskript bleibt unangetastet

Das Skript „Wohnzimmer Regenbogen" schaltet bei Bewegung ein und 60 s nach
Bewegungsende aus. Auf Wunsch des Eigentümers wird es **nicht** ausgehebelt:
„Wohnzimmer an" schaltet ein, die Automatik läuft unabhängig weiter.

## Terrasse: bestätigte Zuordnung (2026-10-27)

Auf ausdrückliche Bestätigung des Eigentümers („terasse (das wäre terasse wled
und feuer)") besteht Terrasse aus **zwei** Lampen:

| Lampe | Datenpunkt |
|---|---|
| WLED Terrasse | `wled.0.84f3ebfb5bf9.on` |
| Feuer (IR/HAM) | `ham.0.Feuer.Switch.On` |

Diese Zuordnung steht in `rooms_overrides.json` und hat **Vorrang** vor der
Ableitung aus Skripten und Aliasen. Der Override ist die einzige Stelle, an der
eine Raum-Zuordnung von einer Aussage des Eigentümers stammt statt aus Daten —
deshalb getrennt geführt und kommentiert.

Live getestet: „terrasse an" schaltet beide in 59 ms, Ausgangszustand
wiederhergestellt (beide false).

Hinweis: Die frühere Kopplung von Terrasse an `Meine_Geräte.0.Terrasse_Regebogen`
(Prozentwert) bleibt als Brücke bestehen, ist aber für das Raum-Routing nicht
mehr maßgeblich.

## Garten ist kein Licht-Raum (2026-10-27)

Bestätigt vom Eigentümer: „garten hat keine beleuchtung oder so. das kannste
raus nehmen."

Garten tauchte nur auf, weil die Wegbeleuchtung unter `alias.0.Garten.*` liegt
— ein Sammelordner, kein eigener Licht-Raum. Umgesetzt über `EXCLUDED_ROOMS`
in `rooms.py`. „garten an" fällt damit wieder an die Cloud bzw. die KI,
„wegbeleuchtung an" schaltet weiterhin lokal.

## Zigbee-Gruppen bevorzugen (2026-10-27)

Belegtes Problem (Eigentümer): der zigbee-Adapter meldet Einzelgeräte mitunter
als `available=false` und schaltet sie dann nicht — dieselbe Lampe ist über ihre
**Gruppe** aber erreichbar.

Messung am Arbeitszimmer:

```
zigbee.0.588e81fffed27066   state=true    available=true
zigbee.0.842e14fffe19daf3   state=false   available=false   ← offline gemeldet
zigbee.0.842e14fffe22f604   state=false   available=true
zigbee.0.group_1.state      state=true                        ← Gruppe sagt "an"
```

Umsetzung:

- `rooms.py` sucht zu jedem Raum eine Zigbee-Gruppe, deren Gerätename dem
  Raumnamen entspricht (`group_1` = "Arbeitszimmer", `group_2` = "Badezimmer",
  `group_3` = "Wegbeleuchtung"). Ergebnis steht als `group` und `preferred`
  in `rooms.json`.
- Der Ausführer schreibt bevorzugt auf die Gruppe statt auf Einzelgeräte.
- `is_available()` warnt, wenn ein Ziel als offline gemeldet wird — leitet aber
  **nicht** um, weil der Nutzer das Ziel genannt hat, nicht wir.

Räume ohne Gruppe (Wohnzimmer, Küche, Schlafzimmer, Terrasse) nutzen weiter
Einzelziele; dort existiert keine passende Zigbee-Gruppe.

Warum Gruppen einzeln adressierten Lampen vorzuziehen sind: eine Gruppenadresse
ist ein einziger Funkbefehl an alle Mitglieder, kein Rundruf an jedes Gerät mit
eigener Zustandsprüfung. Sie umgeht damit genau die Offline-Fehlmeldung.

## KORREKTUR: Wie Zigbee-Gruppen wirklich ticken (2026-10-27)

Zwei frühere Aussagen in dieser Datei waren falsch. Messung entscheidet.

### Falsch war: "Gruppen geben keine Rückmeldung"
`group_1.state` stand 230 h unverändert auf `true`. Daraus schloss ich, der
Adapter melde Gruppen nie zurück. **Falsch.** Er meldet nur bei echter
Wertänderung. Schreiben von `false` aktualisierte sofort:
`val=false, ack=true, lc` frisch.

### Falsch war: "Einzelbefehl auf offline gemeldete Lampe schaltet nicht"
Ich schrieb `true` auf `842e14fffe19daf3.state` und sah HTTP 200 mit
`ack=false` → schloss "Gerät hat nie geschaltet". **Nicht bewiesen.** Was
bleibt: `ack=false` und `from=system.adapter.simple-api.0` heißt, dass keine
Gerätebestätigung vorliegt. Ob die Lampe physikalisch schaltete, konnte ich
nicht sehen.

### Der eigentliche Fund (vom Eigentümer bestätigt)
Ein **Gruppenbefehl schaltet die Lampen physisch**, aktualisiert aber die
**Einzel-Datenpunkte nicht**. Messung nach `group_1 = false`:

```
group_1.state            val=false  ack=true   frisch   ← korrekt
588e81fffed27066.state   val=true   ack=true   34 min   ← FALSCH (Lampe aus)
842e14fffe19daf3.state   val=false  ack=false  test     ← unbestätigt
842e14fffe22f604.state   val=true   ack=true   26 s     ← FALSCH (Lampe aus)
```

Eigentümer bestätigte: „jo, sind aus."

**Konsequenz für den Code:** Nach einem Gruppenbefehl sind die
Einzel-Datenpunkte unbrauchbar. `control.py` setzt dafür `stale_warning` ins
Ergebnis, statt einen falschen Zustand zu behaupten. Die Gruppe ist die
Wahrheit, nicht die Einzellampe.

### Warum Gruppen trotzdem der bessere Weg sind
Eine Gruppenadresse ist ein einziger Funkbefehl an alle Mitglieder. Sie
funktioniert auch dann, wenn der Adapter ein Einzelgerät als `available=false`
führt — genau der Fall, den der Eigentümer beschrieben hat.

## STT-Korrektur: Vorbereitung, keine Fehlerliste (2026-10-27)

Klarstellung, weil es sonst falsch in der Dokumentation stehen bliebe:
„Kuchen statt Küche" war ein **Beispiel des Eigentümers**, um zu erklären, was
er mit unzuverlässigem lokalen STT meint. Es ist **nie passiert**.

`fuzzy.py` heißt deshalb nicht „bekannte Verwechslungen", sondern führt eine
bewusst kurze, begründete Liste:

- Schreibweisen ohne Umlaut (`kuche`) — plausibel bei Diktat/Tastatur
- Doppelkonsonant/Vertauschung (`schlafzimer`, `badzimmer`)
- Zusammengesprochene Gerätenamen (`monitorlich`, `deckentstrahler`)
- das Beispiel des Eigentümers (`kuchen`), ausdrücklich als solches markiert

Entfernt wurden Einträge ohne jeden Beleg (`wolfzimmer`, `gartenbeleutung`,
`kuchenblech`, `kuechenzeile`) — die wären geraten gewesen und hätten echte
Sätze falsch verbiegen können.

**Regel für Erweiterungen:** Nur Einträge aufnehmen, die im Betrieb tatsächlich
aufgetreten sind, mit Datum im Kommentar. Sonst wächst hier eine Wunschliste
statt einer Fehlerliste.

Die Ähnlichkeitskorrektur (Damerau-Levenshtein, Schwelle 0,78) arbeitet
unabhängig davon weiter und fängt Tippfehler wie `schlafzimer` → `Schlafzimmer`
(0,92) oder `deckentstrahl` → `Deckenstrahler` (0,79). `Keller` und `Kissen`
bleiben unangetastet (0,33 bzw. 0,50) — eine niedrigere Schwelle würde dort
falsch eingreifen.
