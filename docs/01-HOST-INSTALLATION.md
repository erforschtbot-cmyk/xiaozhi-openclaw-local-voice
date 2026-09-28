# 1. Host installieren

## Voraussetzungen

- Linux mit systemd-Userdiensten
- OpenClaw `2026.9.6`
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
npm install -g openclaw@2026.9.6
openclaw --version
openclaw models auth login --provider openai
openclaw gateway status
```

Die OpenClaw-Anmeldung ist interaktiv. Keine `secrets.json` von einem fremden Host
kopieren und keine Tokens ins Repository schreiben.

## Bridge installieren

Repository klonen und den tatsächlich im LAN erreichbaren Host angeben:

```bash
git clone git@github.com:erforschtbot-cmyk/xiaozhi-openclaw-voice-terminal.git
cd xiaozhi-openclaw-voice-terminal
./scripts/install-host.sh --public-host 192.168.178.143
```

Der Installer:

1. kopiert beide Bridge-Skripte nach `~/.local/share/jarvis-realtime-bridge`
   (`server.py`, `openclaw-talk-realtime.mjs`, `requirements.txt`);
2. erzeugt eine Python-Umgebung und installiert exakt `requirements.txt`
   (`websockets==15.0.1`, `opuslib==3.0.1`);
3. installiert die systemd-Userunit `jarvis-realtime-bridge.service`
   aus `systemd/jarvis-realtime-bridge.service.in`;
4. aktiviert und startet sie;
5. verändert keine OpenClaw-Zugangsdaten.

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
