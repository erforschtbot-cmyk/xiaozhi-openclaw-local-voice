# 16 — Geräteverwaltung im Browser (`:8088`)

Diese Seite ist **kein eigenes Produkt und kein Dienst von außen**. Sie ist ein
einzelnes Python-Programm aus diesem Repository (`local-smarthome/panel.py`),
das der Host-Installer mitinstalliert und als systemd-Userdienst startet. Sie
dient der Verwaltung (Namen, Räume, Datenpunkte, Gruppen, Satz-Test) — geschaltet
wird aus der Seite heraus **nicht**.

> **Alle IP-Adressen in diesem Repository sind Beispiele des Referenzhauses.**
> `192.168.178.143` ist der OpenClaw-Host, `192.168.178.24` der ioBroker.
> In einer fremden Installation sind beide anders. Wo eine Adresse variiert,
> steht sie als Platzhalter (`<LAN-IP-DES-HOSTS>`, `IOBROKER-IP`); wo eine
> Beispieladresse steht, ist sie ausdrücklich als Beispiel gekennzeichnet.

## Woher die Seite kommt

```text
Repo                         Installationsort                     Dienst
local-smarthome/panel.py  →  ~/.local/share/alexa-direct/panel.py  ← geraeteverwaltung.service
local-smarthome/*.py,*.json  ~/.local/share/alexa-direct/…
systemd/geraeteverwaltung.service.in → ~/.config/systemd/user/geraeteverwaltung.service
```

Die Seite hat **keine Fremdabhängigkeit**: Sie nutzt `http.server` und
`ThreadingHTTPServer` aus der Python-Standardbibliothek. Es gibt kein Flask,
kein FastAPI, kein npm-Paket und keinen Build-Schritt. Der HTML-, CSS- und
JavaScript-Teil liegt als Zeichenkette in `panel.py`; es gibt keine separaten
Template-Dateien.

## Wie sie erzeugt wird

### Automatisch (Normalfall)

`scripts/install-host.sh` erledigt alles in einem Lauf:

1. kopiert die Resolver- und Panel-Quellen nach
   `~/.local/share/alexa-direct/`,
2. kopiert die HassIL-Satzvorlagen (`hassil-data/`) mit,
3. legt `bridges.json` und `rooms_overrides.json` nur an, falls sie fehlen
   (bestehende Betreiberstände bleiben erhalten),
4. erzeugt das Python-venv der Bridge (`~/.local/share/jarvis-realtime-bridge/.venv`)
   und installiert daraus `requirements.txt` (u. a. `hassil`, `pyyaml`),
5. rendert `systemd/geraeteverwaltung.service.in` nach
   `~/.config/systemd/user/geraeteverwaltung.service` und ersetzt dabei:
   - `@ALEXA_DIR@` → `~/.local/share/alexa-direct`
   - `@BRIDGE_VENV@` → `~/.local/share/jarvis-realtime-bridge/.venv`
   - `@IOBROKER_URL@` → `$IOBROKER_SIMPLE_API` (Standard `http://127.0.0.1:8087`)
   - `@PANEL_PORT@` → `$PANEL_PORT` (Standard `8088`)
6. aktiviert und startet `geraeteverwaltung.service` und prüft, dass er
   tatsächlich `active` ist.

Aufruf:

```bash
./scripts/install-host.sh --public-host <LAN-IP-DES-HOSTS>
```

Der Port lässt sich ohne Codeänderung setzen:

```bash
PANEL_PORT=9000 ./scripts/install-host.sh --public-host <LAN-IP-DES-HOSTS>
# oder nur die Unit neu schreiben:
IOBROKER_SIMPLE_API=http://IOBROKER-IP:8087 PANEL_PORT=9000 \
  ./scripts/install-host.sh --public-host <LAN-IP-DES-HOSTS> --no-restart
systemctl --user daemon-reload && systemctl --user restart geraeteverwaltung
```

### Von Hand (zum Verstehen oder für Tests)

```bash
cd local-smarthome
IOBROKER_SIMPLE_API=http://IOBROKER-IP:8087 PANEL_PORT=8088 python3 panel.py
# Ausgabe: Geraeteverwaltung auf http://0.0.0.0:8088  (ioBroker: …)
```

Für den Dauerbetrieb ist die gerenderte Unit maßgeblich; `panel.py` allein
startet keinen Dienst und überlebt kein Abmelden.

## Konfiguration

| Einstellung | Wo | Wirkung |
|---|---|---|
| `PANEL_PORT` | Umgebungsvariable / Installer | Port der Seite (Standard `8088`) |
| `PANEL_HOST` | Umgebungsvariable; in der Unit fest `0.0.0.0` | `0.0.0.0` = alle Netzwerkkarten, `127.0.0.1` = nur dieser Rechner |
| `IOBROKER_SIMPLE_API` | Umgebungsvariable / Unit | Basisadresse des ioBroker Simple API |
| `settings.json` | `~/.local/share/alexa-direct/settings.json` | Oberfläche schreibt die ioBroker-Adresse dorthin; hat Vorrang vor der Umgebungsvariable |
| `names_overrides.json` | ebenda | vergebene Sprechnamen, ausgeblendete Geräte |
| `rooms_overrides.json`, `bridges.json` | ebenda | gepflegte Raum-/Sprachgeräte-Zuordnung |

Reihenfolge der ioBroker-Adresse: `settings.json` → `IOBROKER_SIMPLE_API` →
Referenzwert im Code. Die Seite zeigt an, woher der aktuelle Wert stammt, und
markiert das Feld rot, wenn die Adresse nicht erreichbar ist.

`catalog.json`, `localmap.json` und `rooms.json` werden aus ioBroker erzeugt und
sind **nicht** versioniert; `settings.json` und `names_overrides.json` sind
betreiberspezifisch und ebenfalls nicht im Repository.

## Die URL richtig lesen

```text
http://<LAN-IP-DES-OPENCLAW-HOSTS>:<PANEL_PORT>/
```

- Die IP ist die des **OpenClaw-Hosts**, nicht die des ioBroker und nicht die
  des ESP32.
- `localhost:8088` funktioniert nur auf dem Host selbst; aus dem LAN braucht es
  die LAN-IP und `PANEL_HOST=0.0.0.0`.
- Der Port ist frei wählbar; `8088` ist nur der Standardwert.
- Der Host muss im selben Netz erreichbar sein (Firewall, VLAN, Gäste-WLAN).

Die Seite zeigt ihren eigenen Port und die aufgerufene Adresse oben rechts in
der Kopfzeile — damit ist ein abweichender Port sofort sichtbar.

## Prüfen, ob sie wirklich läuft

```bash
systemctl --user status geraeteverwaltung.service
journalctl --user -u geraeteverwaltung.service -n 30 --no-pager | grep http
ss -ltnp | grep ':8088'
curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8088/
curl -sS -o /dev/null -w '%{http_code}\n' http://<LAN-IP-DES-HOSTS>:8088/
```

Erwartet: Dienst `active (running)`, eine Startzeile mit
`Geraeteverwaltung auf http://0.0.0.0:<port>`, ein LISTEN-Eintrag und zweimal
HTTP `200`.

## Was die Seite technisch tut

```text
Browser ──HTTP──► panel.py (http.server, ein Thread je Anfrage)
                     ├─ liest  localmap.json, catalog.json, rooms.json,
                     │         names_overrides.json, groups.json,
                     │         settings.json, bridges.json
                     ├─ fragt  ioBroker Simple API (getBulk) für Live-Zustände
                     └─ ruft   control.handle(..., dry_run=True) für den Satz-Test
```

Endpunkte: `GET /` (die Seite), `GET /api/devices`, `GET /api/groups`,
`POST /api/rename`, `/api/room`, `/api/change-target` (Alias `/api/target`),
`/api/save-batch`, `/api/delete`, `/api/groups`, `/api/settings`, `/api/test`.
Der Satz-Test ist ausdrücklich `dry_run` und schaltet nichts.

## Sicherheit — bewusst offen, aber nicht ins Internet

- **Kein Login, kein Token, kein HTTPS.** Wer die Seite im LAN öffnen kann,
  kann Sprechnamen, Räume, Datenpunkt-Vorgaben, Gruppen und die ioBroker-Adresse
  ändern.
- **Kein Schalten aus der Seite.** Der Testknopf löst nur auf; echte
  Schaltbefehle laufen über den Sprachweg/Agenten.
- **Nicht ins Internet weiterleiten.** Kein Port-Forwarding, kein Reverse-Proxy
  ohne vorgeschaltete Authentifizierung. Wenn Fernzugriff nötig ist, über VPN
  oder einen authentifizierten Proxy.
- Wer die Oberfläche auf einem Mehrbenutzerhost nur lokal will:
  `PANEL_HOST=127.0.0.1` setzen und die Unit neu starten.

## Wenn etwas nicht stimmt

| Symptom | Ursache / Prüfung |
|---|---|
| `Connection refused` | Dienst nicht gestartet oder an `127.0.0.1` gebunden: `systemctl --user status geraeteverwaltung`; `PANEL_HOST` in der Unit prüfen |
| Dienst startet nicht | Port belegt: `ss -ltnp \| grep ':8088'`; anderen `PANEL_PORT` rendern |
| Seite lädt, Liste ist leer | ioBroker nicht erreichbar: Feld oben rot, `journalctl --user -u geraeteverwaltung -n 50` |
| Geräte fehlen | `catalog.json`/`localmap.json` veraltet oder leer; Kataloglauf prüfen (`jarvis-alexa-catalog.timer`) |
| Änderungen wirken nicht im Sprachweg | Datei am falschen Ort oder Dienst nicht neu gestartet; Ziel ist `~/.local/share/alexa-direct/` |
| Port vergessen | Kopfzeile der Seite oder `journalctl … \| grep http` |

## Abgrenzung zum lokalen Schaltweg

Die Seite ist **nicht** Voraussetzung dafür, dass das Gerät einfache Befehle
schnell und ohne Agentenlauf ausführt. Dafür ist allein der lokale Resolver
zuständig. Wenn im Bridge-Log „Lokaler Smart-Home-Weg nicht nutzbar“ steht,
fehlt die Python-Seite des Resolvers – nicht diese Seite. Diagnose:
[`17-SCHNELLWEG-DIAGNOSE.md`](17-SCHNELLWEG-DIAGNOSE.md).

## Bezug zu den anderen Kapiteln

- Installation und Reihenfolge: [`01-HOST-INSTALLATION.md`](01-HOST-INSTALLATION.md)
- Dateibestand und erzeugte Daten: [`00-DATEIBESTAND.md`](00-DATEIBESTAND.md)
- Bedienung, Gruppen und Smart-Home-Auflösung: [`../local-smarthome/README.md`](../local-smarthome/README.md)
- Smart-Home-Schnellweg ohne Agentenlauf: [`12-SMART-HOME-SCHNELLWEG.md`](12-SMART-HOME-SCHNELLWEG.md)
