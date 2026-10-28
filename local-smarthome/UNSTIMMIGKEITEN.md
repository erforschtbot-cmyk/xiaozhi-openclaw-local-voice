# Alias-Analyse und Unstimmigkeiten

Stand 2026-10-26. Die `alias.0`-Objekte sind **manuell angelegt** (Elzershark)
und bewusst unvollständig. Das erklärt die Lücken — es ist kein Fehler,
sondern offene Arbeit an der Verknüpfung.

## Wie die Zuordnung funktioniert

```
common.smartName.de   ->  Name in der Alexa-App
common.alias.id       ->  echter lokaler Datenpunkt
role                  ->  welche Aktion daran hängt
```

Ein Gerät hat meist mehrere Alias-Geschwister. Der smartName sitzt oft
nur an einem davon — deshalb wird pro Gerätebasis gruppiert.

- Alias-Objekte gesamt: **107**
- Alexa-Geräte aus alexa2: **124**
- davon mit lokalem Ziel: **42**

## 1. Fehlende oder falsche ON-Aliase (das ist die eigentliche Lücke)

### 1a. Lampe Küche (Philips Hue, 9290012573A) — kein ON-Alias vorhanden
Es gibt nur DIMMER, RGB, TEMPERATURE. Der passende Datenpunkt
`zigbee.0.0017880103544b64.state` **existiert und ist schreibbar**.

**Erledigt:** `alias.0.Küche.Lampe_Küche.ON` am 2026-10-26 angelegt
(Ziel `zigbee.0.0017880103544b64.state`, `role: switch.light`). Live geprüft
über den Alias: schaltet die Lampe, 10 ms. Ausgangszustand wiederhergestellt.

Zusätzlich leitet der Resolver Ein/Aus aus dem Schwester-Alias ab, falls ein
`ON`-Mitglied fehlt — für künftige Geräte ohne ON.

### 1b. Lampe Schlafzimmer (Tuya TS0505B_1) — ON zeigt ins Leere
`ON -> zigbee.0.0017880103546aa8.state` (existiert nicht),
Farbe/Dimmen zeigen auf `zigbee.0.a4c138ae1ecf2c06.*`.

**Erledigt:** Das `ON`-Mitglied wurde am 2026-10-26 von
`zigbee.0.0017880103546aa8.state` (existiert nicht) auf
`zigbee.0.a4c138ae1ecf2c06.state` umgebogen und mit `role: switch.light`
versehen. Live geprüft über den Alias: an und wieder aus, 10 ms.
Backup des Originals: `backup-alias/alias_0_Schlafzimmer_Lampe_Schlafzimmer_ON.json`.

### 1c. Alle Lampen — Gruppe existiert nicht (von dir als ungenutzt bestätigt)
Alle vier Geschwister zeigen auf `zigbee.0.group_4.*`.
Es gibt nur **group_1, group_2, group_3**. group_4 fehlt komplett.

## 2. Leere Alias-Gerüste (angelegt, nie verknüpft)

| Objekt | common.alias.id |
|---|---|
| `alias.0.Arbeitszimmer.Geräte.SET` | *(leer)* |
| `alias.0.Wohnzimmer.Gerät_1.SET` | *(leer)* |

## 3. Fremd-Importe mit krummen Namen (vom wled-Adapter)

Diese habe ich nicht angelegt und sie haben keinen smartName — sie
kollidieren aber mit der Namenslogik:

| Alias | Ziel |
|---|---|
| `alias.0.Brightness of the light.bri` | `wled.0.5ccf7f81c758.bri` |
| `alias.0.On / Off.on` | `wled.0.5ccf7f81c758.on` |
| `alias.0.Wohnled_rgb.0_HEX` | `wled.0.5ccf7f81c758.seg.0.col.0_HEX` |

## 4. Schalter vorhanden, aber nicht an Alexa gehängt

Diese Geräte haben ein schaltbares `.POWER`/`.ON`-Geschwister, das keinen
smartName trägt — der smartName sitzt am `.SET`/`.ACTUAL`. Dadurch war der
Ein/Aus-Weg zuerst unsichtbar. Inzwischen aufgelöst (35/42 statt 33/42).

| Gerät | schaltbares Geschwister | Ziel |
|---|---|---|
| Temperatur Klimaanlage | `.POWER` | `0_userdata.0.Klimaanlage.power` |
| Lampe Badezimmer | `.ON` | `zigbee.0.group_2.state` |
| Wegbeleuchtung | `.ON` | `zigbee.0.group_3.state` |
| Deckenstrahler links | `.ON` | `zigbee.0.a4c13834b389ea43.state` |
| Deckenstrahler rechts | `.ON` | `zigbee.0.a4c138364a76c01a.state` |
| Lüfter | `.POWER` | `zigbee.0.7cb03eaa0a0c3813.state` |

## 5. Doppelte Alexa-Namen (bleiben bewusst unlokalisiert)

| Name | Anzahl |
|---|---|
| banane1 | 3 |
| Heizung | 2 |
| Stunde | 2 |
| Uhr | 2 |
| Ventilator | 2 |

## 6. Nur Dimmen/Farbe, kein Ein/Aus lokal

| Gerät | lokale Fähigkeiten |
|---|---|
| Ladezeit | brightness |
| Lampe Küche | brightness, color, colortemp |
| Lampe Schlafzimmer | brightness, color, colortemp |
| Stunde | setpoint |
| Uhr | brightness, colortemp |

## 7. Was jetzt lokal geht

**35 von 42** zugeordneten Geräten routen lokal. Die 7 Ausnahmen:

| Gerät | Grund |
|---|---|
| Lampe Küche | kein ON-Alias → siehe 1a |
| Lampe Schlafzimmer | ON zeigt ins Leere → siehe 1b |
| Heizung, Stunde, Uhr, Licht Arbeitszimmer | doppelter Alexa-Name → mehrdeutig |
| Ladezeit | nur Dimmen, kein Ein/Aus |
