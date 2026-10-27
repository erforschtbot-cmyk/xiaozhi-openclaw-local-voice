# 1. Host installieren

> Zuerst `00-START-HIER.md`: diese Anleitung setzt bereits eingerichtetes TLS,
> Token, Provider und ein funktionierendes Talk-/Consult-Ziel voraus; sie ist kein kompletter Bootstrap.

## Voraussetzungen

- Linux mit systemd-Userdiensten
- OpenClaw `2026.10.7` (oder eine Version, für die unter `patches/` ein Satz liegt)
- Node.js und npm
- Python 3 mit `venv` (Referenzhost: `3.14.7`)
- `libopus`
- funktionierender OpenClaw-Gateway auf `wss://127.0.0.1:18789`
- ein in OpenClaw angemeldeter OpenAI-Provider mit `gpt-live-1-codex`

Arduino IDE ist für dieses Projekt **nicht** erforderlich. Die Firmware ist ein
ESP-IDF-Projekt und wird reproduzierbar im Docker-Container gebaut.

Arch Linux:

```bash
sudo pacman -S --needed nodejs npm python python-pip opus docker git
```

Debian/Ubuntu:

```bash
sudo apt update
sudo apt install -y nodejs npm python3 python3-venv libopus0 docker.io git
```

OpenClaw installieren und anmelden:

```bash
npm install -g openclaw@2026.10.7
openclaw --version          # muss 2026.10.7 zeigen
openclaw gateway status     # muss laufen
```

Danach den Provider anmelden und prüfen, dass das Gateway wirklich auf
`wss://127.0.0.1:18789` erreichbar ist:

```bash
openclaw models auth login --provider openai
ss -ltn | grep 18789        # muss lauschen
```

**Prüfpunkt:** `openclaw --version` und die laufende Gateway-Version müssen
zusammenpassen. Weichen sie ab, läuft noch der alte Prozess — dann erst
`openclaw gateway restart`, bevor es weitergeht.

Die OpenClaw-Anmeldung ist interaktiv. Keine `secrets.json` von einem fremden Host
kopieren und keine Tokens ins Repository schreiben.

## Bridge installieren

Repository klonen und den tatsächlich im LAN erreichbaren Host angeben:

```bash
git clone git@github.com:erforschtbot-cmyk/xiaozhi-openclaw-local-voice.git
cd xiaozhi-openclaw-local-voice
./scripts/install-host.sh --public-host 192.168.178.143
```

Der Installer:

1. kopiert **alle** Laufzeitmodule nach `~/.local/share/jarvis-realtime-bridge`.
   Das sind nicht nur die Einstiegsdateien: `server.py` importiert
   `room_command.py` hart, der Gateway-Patch importiert `voice-turn-context.mjs`
   und `scoped-smart-home-router.mjs` statisch. Fehlt eine davon, startet die
   Bridge nicht bzw. der Gateway-Handler lädt nicht mehr;
2. legt `room-map.json` aus einer **Vorlage** an, falls noch keine existiert
   (bestehende Zuordnungen bleiben unangetastet);
3. legt `disable-smart-home-fastpath` an — eine leere Datei, die den alten
   Bridge-Direktpfad abschaltet, damit genau ein Ausführer schaltet;
4. installiert die Offline-Tests mit, weil die Prüfskripte sie am Zielort
   erwarten;
5. erzeugt eine Python-Umgebung aus `requirements.txt`
   (`websockets==15.0.1`, `opuslib==3.0.1`) und, bei lokaler Stimme, aus
   `requirements-tts.txt` (`piper-tts==1.4.2`);
6. schreibt `jarvis-realtime-bridge.service` aus der Vorlage und ersetzt dabei
   auch `OPENCLAW_NPM_ROOT` (ermittelt über `npm root -g`) und die Session-Keys;
7. aktiviert und startet den Dienst;
8. **prüft sich selbst:** jede erwartete Datei einzeln und danach die echte
   Python-Importkette. Bei einem Fehler bricht er ab, statt einen Dienst zu
   starten, der beim ersten Ton stirbt.

**Prüfpunkt:** Am Ende muss `import OK: server.py inkl. room_command, opuslib,
websockets` erscheinen und der Dienst `active` sein. Kommt stattdessen
`MISSING:` oder `Importkette FEHLGESCHLAGEN`, ist der Nachbau an dieser Stelle
kaputt — nicht weiterbauen, sondern die genannte Datei suchen.

Abweichender Installationsort:

```bash
./scripts/install-host.sh --public-host 192.168.178.143 --install-dir "$HOME/.local/share/jarvis-realtime-bridge"
```

Danach:

```bash
systemctl --user status jarvis-realtime-bridge.service
journalctl --user -u jarvis-realtime-bridge.service -n 100 --no-pager
ss -ltn | grep -E ':8765|:8766'
```

Ports:

- `8765/tcp`: XiaoZhi-WebSocket (Audio + Protokoll)
- `8766/tcp`: lokale OTA-Konfiguration, die das Gerät auf Port 8765 verweist

Der Host muss aus dem WLAN des ESP32 unter der bei `--public-host` angegebenen
Adresse erreichbar sein.

## Reihenfolge

Der Bridge-Dienst startet nur dann sinnvoll, wenn der OpenClaw-Gateway läuft — die
Unit enthält `After=openclaw-gateway.service`. Zusätzlich ist der `dist`-Patch aus
`docs/03-OPENCLAW-PATCH.md` nötig, damit die Sprachbestätigung und die
Transkript-Persistenz dem Ist-Stand entsprechen.
