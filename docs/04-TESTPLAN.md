# 4. Vollständiger Testplan

Ein Neuaufbau gilt erst als fertig, wenn alle Punkte bestanden sind.

## A. Infrastruktur

```bash
./scripts/verify-host.sh
systemctl --user is-active openclaw-gateway.service
systemctl --user is-active jarvis-realtime-bridge.service
```

Im Bridge-Journal müssen nach Geräteverbindung `XiaoZhi connected from`,
`Device MCP initialized` und ein `ready`-Ereignis mit `consultSessionKey`
erscheinen.

## A2. Animiertes Gesicht

- In Bereitschaft: Augen bewegen sich gelegentlich und blinzeln; die Darstellung
  atmet leicht.
- Nach „Jarvis“: Augen werden sichtbar aufmerksamer.
- Beim Sprechen: der ruhende Mund wechselt auf drei animierte Balken.
- Statusleiste und Untertitel bleiben über dem Gesicht sichtbar.

Bestanden nur, wenn das Display stabil bleibt und keine Reboots, Watchdog- oder
LVGL-Fehler auftreten.

## B. Schnelle direkte Antwort (ohne Werkzeug)

Sprich: **„Jarvis, erzähle einen Witz."**

Erwartet: unmittelbare gesprochene Antwort, danach Bereitschaft. Kein
OpenClaw-Werkzeug nötig.

## C. Geräte-MCP

Sprich: **„Jarvis, Lautstärke auf 50 Prozent."**

Erwartet: sofortige Änderung, Anzeige `Lautstärke: 50 %`, keine Rückfrage.

Sprich: **„Jarvis, Helligkeit auf 40 Prozent."**

Erwartet: sofortige Änderung und Anzeige.

## D. Erstes Werkzeug nach einer Pause (der kritische Test)

1. Etwa 60–120 Sekunden warten, bis das Gerät wieder in Bereitschaft ist.
2. Sprich: **„Jarvis, wie spät ist es?"**
3. Zwischenansage abwarten, dann die Zeit hören.

Bestanden nur, wenn **keine** Meldung „Es hat leider nicht geklappt … soll ich es
nochmal?" erscheint und im Gateway-Journal **kein**
`SqliteTranscriptMutationConflictError` steht.

Zur Kontrolle, dass der Fehler wirklich ausbleibt:

```bash
journalctl --user -u openclaw-gateway.service --since "5 min ago" --no-pager \
  | grep -c SqliteTranscriptMutationConflictError
# erwartet: 0
```

Ohne Patch B schlägt genau dieser Schritt reproduzierbar fehl.

## E. Folgefrage ohne neues Wakeword

Direkt nach der Antwort aus D fragen: **„Und wie spät ist es jetzt?"**

Erwartet: keine erneute Werkzeug-Rückfrage mit Fehler; Antwort kommt.

## F. OpenClaw-Lesewerkzeug

Sprich eine Anfrage, die eine Websuche verlangt.

Erwartet: Werkzeugaufruf im OpenClaw-Gateway-Journal und anschließend gesprochene
inhaltliche Antwort.

## G. Rückkehr nach Bereitschaft

Sprich eine Werkzeugfrage und warte das Ende ab. Danach prüfen:

```bash
journalctl --user -u jarvis-realtime-bridge.service -n 20 --no-pager | tail -8
```

Erwartet: `Follow-up window expired; returning to standby` und ein neuer
`XiaoZhi connected from`. Das Gerät ist danach wieder ansprechbar.

## H. 24/7 und Reconnect

- länger als fünf Minuten warten: kein PMIC-Off/Standby;
- WLAN kurz unterbrechen: Gerät muss gespeicherte Netze selbstständig erneut
  verbinden;
- Wakeword danach erneut testen.

## J. Wiederherstellungs-Watchdog

Prüft, dass das Gerät aus einem hängenden Gesprächszustand selbstständig nach
Bereitschaft zurückkehrt — ohne Neustart der Bridge.

1. Eine Werkzeugfrage stellen und den Ablauf so weit es geht ausbremsen (z. B. eine
   Anfrage, die serverseitig lange braucht), bis das Display in „Sprechen“ oder
   „Zuhören“ stehen bleibt.
2. Warten, ohne etwas anzufassen (max. 120 s).
3. Im seriellen Log oder im Bridge-Journal muss erscheinen:

```text
Stuck watchdog: state=speaking in_state=61s idle=60s (no server audio while speaking) -> idle
```

4. Danach muss das Gerät **ohne Fremdeingriff** wieder in Bereitschaft sein und auf
   „Jarvis“ reagieren.

Bestanden nur, wenn kein Bridge-Neustart nötig war. Zum Vergleich ohne Watchdog:
Der Zustand bleibt stehen, bis der Socket von außen abgebrochen wird.

## K. Hänger-Auflösung (Gegenprobe)

Wiederhole den Ablauf, wenn der Watchdog bewusst umgangen werden soll. Dann:

```bash
systemctl --user restart jarvis-realtime-bridge.service
```

Erwartet: Nach wenigen Sekunden verbindet sich das Gerät selbst wieder
(`XiaoZhi connected from`). Das bestätigt, dass nicht die Firmware „hängt", sondern
dass ein Socket-Abbruch das Gerät zurück in Bereitschaft bringt.
