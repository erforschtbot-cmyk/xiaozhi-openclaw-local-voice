# 18 — Andere Hosts, Container und getrennte Gateways

Die Referenzarchitektur ist **ein Host**: OpenClaw-Gateway und Bridge laufen
unter demselben Benutzer, die Gateway-Patches importieren die Bridge-Module per
absolutem Pfad. Dieses Kapitel beschreibt, was davon konfigurierbar ist, was
nicht – und welche Fragen eine KI dem Betreiber stellen muss, statt Annahmen zu
treffen.

## IP-Legende für Menschen und KI-Agenten

Nicht jede IP-Adresse in diesem Repository ist dieselbe Art von Wert:

| Schreibweise | Bedeutung | Auf einem fremden System übernehmen? |
|---|---|---|
| `192.168.178.143` | historische Adresse des OpenClaw-/Bridge-Hosts im Referenznetz | **Nein.** Echte, aus dem Geräte-WLAN erreichbare Host-Adresse ermitteln und als `--public-host` angeben. |
| `192.168.178.24` | historische Adresse des ioBroker im Referenznetz | **Nein.** Betreiber nach ioBroker-Host und Simple-API-Port fragen. |
| `192.168.68.157`, `192.168.1.10`, `10.0.0.5` | Beispiele bzw. Testdaten für andere private Netze | **Nein.** Sie beweisen nur, dass andere Subnetze unterstützt werden. |
| `127.0.0.1` / `localhost` | der Rechner, auf dem der jeweilige Prozess läuft | Nur übernehmen, wenn Dienst und Aufrufer wirklich auf demselben Host laufen. In einem Container bezeichnet es den Container selbst. |
| `0.0.0.0` | Bind-Adresse: auf allen lokalen Netzwerkkarten lauschen | Nie als Zieladresse in Browser, Firmware oder Client einsetzen. |
| `<HOST-IP>`, `<NAS-IP>`, `IOBROKER-IP` | absichtlicher Platzhalter | Vor Ausführung durch einen echten Betreiberwert ersetzen; spitze Klammern nicht mitsenden. |

**Entscheidungsregel für eine KI:** Eine private Adresse aus dem Repository ist
kein Hinweis darauf, dass dieses Ziel im aktuellen Netz existiert. Erst
Topologie feststellen, dann Werte einsetzen, danach Erreichbarkeit prüfen. Bei
fehlender Information den Betreiber fragen statt Adressen zu erfinden.

## Harte Kopplungen und konfigurierbare Werte

Die Netzwerkwerte sind konfigurierbar; einige andere Teile des
Referenzaufbaus sind es noch nicht vollständig:

- `--public-host` ist eine Pflichtangabe für Bridge/Firmware und muss aus dem
  Geräte-WLAN erreichbar sein. Das ist **keine** Adresse des KI-Systems.
- ioBroker wird über Oberfläche, `JARVIS_IOBROKER_URL` oder
  `IOBROKER_SIMPLE_API` gesetzt; die Referenz-IP ist nur Dokumentation/Rückfall
  für den Originalaufbau.
- `OPENCLAW_GATEWAY_URL=wss://127.0.0.1:18789` setzt voraus, dass Helfer und
  Gateway auf demselben Host laufen. Bei getrennten Hosts ist URL plus
  TLS-Vertrauen anzupassen.
- Die OpenClaw-Patches enthalten absolute Modulpfade. Ein anderer Benutzer,
  Home-Pfad, Container oder Gateway-Host verlangt einen passend neu erzeugten
  Patchsatz; bloß eine IP zu ändern genügt nicht.
- Session-Keys, Echo-Geräte-ID, MAC-/Raumzuordnung, WLAN, Token und Provider
  gehören zum Betreiber. Sie dürfen nicht aus dem Referenzsystem übernommen
  oder geraten werden.

Damit ist eine **Referenzkonfiguration** nicht mit einer
**Installationsvorgabe** gleichzusetzen. `.reference`-Dateien dokumentieren den
Originalhost; `.in`-Vorlagen und Installer sind für den Zielhost bestimmt.

## Kurzentscheidung

| Situation | Vorgehen |
|---|---|
| Ein Host, Benutzer `/home/openclaw` | Standard; nichts zu tun |
| Ein Host, anderer Benutzer/Home | `JARVIS_DATA_ROOT` und `JARVIS_SMARTHOME_DIR` setzen; Gateway-Patch für den echten Pfad neu erzeugen |
| Bridge im Container, Gateway auf demselben Rechner | Datenwurzel als Volume an denselben Pfad binden; `OPENCLAW_GATEWAY_URL` auf den Host zeigen lassen |
| Bridge auf NAS, Gateway auf anderem Rechner | **nicht** unterstützt ohne Umbau: Katalog, `last-utterance`, `active-room` und der Patch-Pfad liegen auf dem Gateway-Host. Empfohlen: Bridge + Gateway zusammen, nur die STT per `--stt-uri` auslagern |
| Nur Audio-Board | `jarvis-screen` (8770) nicht nötig; 8088 optional |

## Konfigurierbare Werte

Alle Dienste lesen eine gemeinsame Datei
`<Installationsordner>/jarvis.env`. Der Installer legt sie an und aktualisiert
nur bekannte Schlüssel; eigene Zeilen bleiben erhalten.

| Variable | Standard | Wirkung |
|---|---|---|
| `JARVIS_DATA_ROOT` | `~/.local/share/jarvis-realtime-bridge` | Bridge-Daten, Katalog, `last-utterance`, `active-room`, Turn-Gates, Fotos |
| `JARVIS_SMARTHOME_DIR` | `~/.local/share/alexa-direct` | Resolver, `smarthome_cli.py`, `settings.json`, Namen/Räume |
| `JARVIS_IOBROKER_URL` | `http://127.0.0.1:8087` (frisch) bzw. Referenzwert | ioBroker Simple API |
| `JARVIS_ALEXA_ENDPOINT` | – | komplette textCommand-URL; überschreibt alles andere |
| `JARVIS_ECHO_DEVICE_ID` | Referenz-Echo | Echo-Geräte-ID im textCommand-Pfad |
| `OPENCLAW_GATEWAY_URL` | `wss://127.0.0.1:18789` | Gateway-Adresse für Helfer und Relay |
| `OPENCLAW_SECRETS_FILE` | `~/.openclaw/secrets.json` | Token-Datei |
| `NODE_EXTRA_CA_CERTS` | vom Dienst gesetzt | CA für TLS |
| `PANEL_PORT` / `PANEL_HOST` | `8088` / `0.0.0.0` | Geräteverwaltung |
| `IOBROKER_SIMPLE_API` | – | älterer Name; wird weiter unterstützt |

Reihenfolge beim ioBroker-Ziel: `settings.json` → `JARVIS_IOBROKER_URL` →
`IOBROKER_SIMPLE_API` → Referenzwert. Ein Eintrag in der Oberfläche gewinnt
also bewusst gegen die Umgebung.

## Was der Installer prüft – und was er fragt

Der Installer installiert auch dann weiter, wenn der ioBroker nicht erreichbar
ist. Am Ende gibt er aus:

- Ergebnis des lokalen Dry-Runs (`ok: true` oder ein `reason`),
- ob die ioBroker-Adresse erreichbar ist,
- einen Hinweis, wenn `OPENCLAW_GATEWAY_URL` nicht auf `127.0.0.1` zeigt.

Ist der ioBroker nicht erreichbar, ist das **kein Installationsfehler**: Sprache
und Agent funktionieren weiter, der lokale Schnellweg fällt auf den Agenten
zurück, und die Geräteliste bleibt leer. Die KI muss dann fragen, nicht raten.

## Fragen, die eine KI stellen muss

1. Steht ein ioBroker bereit, und unter welcher Adresse/Port?
2. Welche Echo-Geräte-ID trägt der textCommand-Datenpunkt? (Nur nötig, wenn der
   Schnellweg über Alexa laufen soll.)
3. Läuft das OpenClaw-Gateway auf demselben Host wie die Bridge?
4. Unter welchem Benutzer/Home läuft der Gateway-Host? (Patch-Pfade sind absolut.)
5. Welches Board hängt am USB: `4b` oder `audio`?
6. Welche LAN-IP hat der Host, und ist sie aus dem Geräte-WLAN erreichbar?
7. Sind 8088/8770/8765/8766 frei?
8. Ist der Provider angemeldet, und liegt ein Gateway-Token vor?

Ohne Antwort auf 3 und 4 ist kein belastbarer Aufbau möglich; ohne 1 und 2
funktioniert Sprache, aber nicht das lokale Schalten.

## Container-Betrieb

Die Bridge kann in einem Container laufen, solange die Datenwurzel und die
Gateway-Sicht zusammenpassen:

```bash
docker run ... \
  -e JARVIS_DATA_ROOT=/data/jarvis \
  -e JARVIS_SMARTHOME_DIR=/data/alexa-direct \
  -e JARVIS_IOBROKER_URL=http://IOBROKER-IP:8087 \
  -e OPENCLAW_GATEWAY_URL=wss://GATEWAY-HOST:18789 \
  -e OPENCLAW_SECRETS_FILE=/secrets/secrets.json \
  -v /srv/jarvis:/data/jarvis \
  -v /srv/alexa-direct:/data/alexa-direct \
  -v /srv/secrets.json:/secrets/secrets.json:ro \
  ...
```

Wichtig:

- **Der Patch bleibt absolut.** `voice-turn-context.mjs` und
  `scoped-smart-home-router.mjs` werden vom Gateway per `file:///…` geladen.
  Der Gateway-Host braucht diese Dateien an genau dem Pfad, der im Patch steht.
  Ein anderer Pfad verlangt einen neu erzeugten Patchsatz samt neuen Hashes.
- **Katalog und `last-utterance` müssen für die Weiche sichtbar sein.** Die
  Weiche läuft im Gateway, nicht im Bridge-Container. Bei getrennten Hosts
  braucht sie ein gemeinsames Volume oder einen anderen Transport – sonst
  meldet sie `catalog-unavailable`.
- **Ein getrenntes Gateway braucht TLS-Vertrauen.** `NODE_EXTRA_CA_CERTS` auf
  die CA des Gateways setzen; der Token muss zur Gegenstelle passen.

## Getrennte Erkennung statt getrennter Bridge

Der saubere Weg, Rechenlast auszulagern: Bridge und Gateway bleiben zusammen,
nur die STT läuft woanders.

```bash
./scripts/install-host.sh --public-host <HOST-IP> --stt-uri <NAS-IP>:10303
```

Die Bridge fragt dann die Wyoming-Schnittstelle über das LAN ab. Piper läuft
weiter lokal; für die kleinen Stimmen genügt die CPU des Gateway-Hosts.

## Prüfen

```bash
systemctl --user is-active jarvis-realtime-bridge geraeteverwaltung jarvis-screen
ss -ltnp | grep -E ':8088|:8765|:8766|:10303'
curl -sS -o /dev/null -w '%{http_code}\n' http://<HOST>:8088/
SMARTHOME_DRY_RUN=1 <BRIDGE>/.venv/bin/python <SMARTHOME>/smarthome_cli.py "schalte den Monitor aus"
```

Erwartet: Dienste `active`, Ports lauschen, HTTP 200, Dry-Run liefert JSON.
Wenn der Dry-Run `fehler:URLError` meldet, ist der ioBroker nicht erreichbar –
das ist die Frage aus Punkt 1, kein Softwarefehler.
