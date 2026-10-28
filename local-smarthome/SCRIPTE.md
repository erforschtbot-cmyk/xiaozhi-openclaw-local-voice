# JavaScript-Adapter: was die Skripte wirklich verknüpfen

Stand 2026-10-26, alle 78 Skripte gelesen.

| | |
|---|---|
| Skripte gesamt | 78 |
| Blockly | 66 |
| JavaScript | 12 |
| aktiv | 68 |
| deaktiviert | 10 |

Blockly-Skripte werden als generiertes JavaScript gespeichert, deshalb sind
beide Typen mit denselben Mustern auswertbar (`setState`, `getState`, `on`).

## 1. Wichtigster Befund: kein Skript schaltet über Alexa

**0** Skripte schreiben auf `alexa2.0.Smart-Home-Devices.*.powerState` oder
`active`. Alles läuft lokal. Der Alexa-Weg ist also nicht durch Skripte
verknotet — das ist eine gute Nachricht fürs lokale Routing.

## 2. Geräte, die ein Skript auslösen (Schreiben löst Logik aus)

Diese 6 sind die kritischen: wer den lokalen Datenpunkt schreibt, startet
ein Skript, das **mehr** tut als nur zu schalten.

| Gerät | lokales Ziel | auslösendes Skript |
|---|---|---|
| Echo Button Kamin | `0_userdata.0.Alexa.Echo_Button_Kamin` | Kamin |
| Kino Arbeitszimmer | `0_userdata.0.Arbeitszimmer.Kino_Arbeitszimmer` | Kino Arbeitszimmer |
| Lampe Küche | `zigbee.0.0017880103544b64.brightness` | Küche |
| Rasensprenger | `0_userdata.0.Garten.Rasensprenger` | Rasensprenger |
| Rasensprenger Timer | `0_userdata.0.Garten.Rasensprenger_Timer` | Rasensprenger |
| Tisch | `0_userdata.0.Wohnzimmer.tisch_led` | Tisch LED |
| Trigger | `0_userdata.0.Alexa.Trigger` | Alexa Trigger |

## 3. Kollisionen: zwei Stellen schreiben dasselbe Ziel

Ein aktives Skript schreibt auf einen Datenpunkt, den ich auch lokal
ansteuern würde. Das ist **kein Fehler**, aber es bedeutet: die Skript-Logik
kann meinen Befehl überschreiben oder ergänzen.

| Skript (aktiv) | schreibt | ist lokales Ziel von |
|---|---|---|
| Brause | `sonoff.0.Garten.POWER2` | Rasensprenger links |
| Gartenbeleuchtung | `zigbee.0.group_3.brightness` | Wegbeleuchtung |
| Kaffeewasser | `sonoff.0.Kaffewasser.POWER1` | Kaffeewasser |
| Küche | `zigbee.0.0017880103544b64.brightness` | Lampe Küche |
| OfflineAssist | `0_userdata.0.Arbeitszimmer.Kino_Arbeitszimmer` | Kino Arbeitszimmer |
| Rasensprenger | `0_userdata.0.Garten.Rasensprenger` | Rasensprenger |
| Whatsapp | `sonoff.0.drucker_91801F.POWER` | Drucker |
| Wohnzimmer Regenbogen | `zigbee.0.a4c13834b389ea43.state` | Deckenstrahler links |
| Wohnzimmer Regenbogen | `zigbee.0.a4c138364a76c01a.state` | Deckenstrahler rechts |
| event | `zigbee.0.7cb03eaa0a0c5657.state` | Steckdose Sofa |

## 4. Von Skripten referenziert, aber existiert nicht (29)

Diese Skripte greifen ins Leere — entweder Gerät gelöscht, umbenannt oder
Adapter entfernt. Betroffene Skripte laufen ins Nichts oder werfen Fehler.

### 4a. Echte fehlende Datenpunkte

| fehlender Datenpunkt | betroffene Skripte |
|---|---|
| `0_userdata.0.Jalousie.Jallo1` | rolladen, rolladen1 |
| `0_userdata.0.Jalousie.Jallo1A` | rolladen, rolladen1 |
| `0_userdata.0.Jalousie.JalloReset` | rolladen, rolladen1 |
| `alexa2.0.Echo-Devices.G000RA08848204QA.Commands.speak` | Waschmaschine |
| `alias.0.Aquarium.Osmose-Wasser_Switch` | Dummy |
| `alias.0.Aquarium.Temperatur_Links` | Dummy |
| `alias.0.Aquarium.Wasserablauf_Switch` | Dummy |
| `alias.0.Aquarium.Wasserpumpe` | Dummy |
| `alias.0.Aquarium.Wasserwechsel_Switch` | Dummy |
| `alias.0.Lampe_Badezimmer.DIMMER` | Skript „Osram Switch" (Skriptname, nicht die Hardware) |
| `javascript.0.scriptEnabled.Aquarium.Wasserkühlung` | Dummy |
| `mqtt.0.rcr.voice` | OfflineAssist |
| `ping.0.elzershark-LIFEBOOK-E736.192_168_68_151` | Anwesend_Olli |
| `sonoff.0.Kinoled.Dimmer` | Kino Regenbogen |
| `sonoff.0.Kinoled.POWER` | Status |
| `sonoff.0.Kinoled.Scheme` | Kino Regenbogen |
| `sonoff.0.Wohnled.Dimmer` | Status |
| `sonoff.0.Wohnled.POWER` | Status |
| `tibberlink.0.Homes.a21f001c-933b-487c-935a-7147439606ea.PricesToday.` | Tibber Diagramm |
| `tibberlink.0.Homes.a21f001c-933b-487c-935a-7147439606ea.PricesTomorrow.` | Tibber Diagramm |
| `zigbee.0.0017880103546aa8.brightness` | WeckerLicht |
| `zigbee.0.60a423fffeff4db1.moving` | rolladen, rolladen1 |
| `zigbee.0.60a423fffeff4db1.position` | rolladen, rolladen1 |
| `zigbee.0.a4c138cddc2147cb.temperature` | Klimaanlage |
| `zigbee.0.a4c138eb2443c6d1.motion_state` | Klimaanlage |
| `zigbee.0.a4c138eb2443c6d1.presence` | Status |

### 4b. Parser-Artefakte (kein Defekt, nur Namensgleichheit im Code)

- `data` in: Balkonkraftwerk
- `end` in: Balkonkraftwerk
- `error` in: Echo Wall Clock

## 5. Deaktivierte Skripte, die Schaltlogik enthalten (10)

Nicht aktiv, aber vorhanden — beim Umbau beachten:

| Skript | hört auf | schreibt |
|---|---|---|
| Klimaanlage | zigbee.0.a4c138cddc2147cb.temperature | zigbee.0.70b3d52b6007a845.state |
| KlimaanlageWinter | 0_userdata.0.Arbeitszimmer.Heizung, 0_userdata.0.Arbeitszimmer.Klima_A | 0_userdata.0.Arbeitszimmer.Klima_Arbeitszimmer, 0_userdata.0.Arbeitszimmer.Tempe |
| Klingel Haustür | alias.0.Garten.Haustür.SET | alexa2.0.Echo-Devices.G6G2MM12449402WK.Commands.speak, zigbee.0.804b50fffe047006 |
| Zigbee | 0_userdata.0.Allgemein.Zigbee | 0_userdata.0.Allgemein.Zigbee, system.adapter.zigbee.0.alive |
| rolladen | 0_userdata.0.Jalousie.Jallo1A, 0_userdata.0.Jalousie.JalloReset, zigbe | 0_userdata.0.Jalousie.Jallo1A |
| rolladen1 | 0_userdata.0.Jalousie.Jallo1A, 0_userdata.0.Jalousie.JalloReset, zigbe | 0_userdata.0.Jalousie.Jallo1A |
