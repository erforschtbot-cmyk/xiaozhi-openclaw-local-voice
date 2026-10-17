# Jarvis-Ring: visuelle Zustandssprache für das ESP32-S3-AUDIO-Board

Die nackte Platine hat **kein Display**, nur den **7er-RGB-Ring** (WS2812,
GPIO 38). Der Ring ist damit die einzige Ausgabe — er trägt die gesamte
Zustandskommunikation.

## Was der Ring zeigt

| Zustand | Farbe | Bewegung | Bedeutung für den Nutzer |
|---|---|---|---|
| Bereitschaft (idle) | blau, gedimmt | ruhiges Atmen | Gerät ist da, hört aber nicht mit |
| WLAN einrichten | blau | langsames Blinken | braucht Einrichtung |
| Verbinden | weiß | schnelles Blinken | Kontakt wird aufgebaut |
| **Zuhören** | **grün** | **Licht läuft im Kreis** | **jetzt sprechen** |
| Sprechen | cyan | Atmen | Antwort läuft |
| Hinweis/Ton | gelb | kurzes Blinken | kurze Meldung |
| Update | violett | schnelles Laufen | nicht ausschalten |
| Aktivierung | bernstein | Blinken | einen Moment |
| Fehler | **rot, stehend** | — | etwas ist schief |

Leitidee: **Farbe = Rolle, Bewegung = Aktivität.** „Zuhören" ist bewusst die
einzige Stufe mit *laufendem* Licht — das ist aus einiger Entfernung die
auffälligste Bewegung und markiert genau den Moment, in dem gesprochen werden
soll. „Bereitschaft" atmet nur, steht nicht — im dunklen Raum auffindbar, ohne
zu blenden.

## Technisch

`jarvis_ring_led.{h,cc}` leitet von der Upstream-Klasse `CircularStrip` ab und
überschreibt nur `OnStateChanged()`. Der Board-Patch ersetzt die nackte
`CircularStrip`-Instanz durch `JarvisRingLed`.

Die sieben LEDs sind physisch als Ring angeordnet; die Upstream-Anzahl in
`esp32-s3-audio_board.cc` steht auf `6` und wird vom Patch auf `7` korrigiert
(die Doku nennt 7 Ring-LEDs).
