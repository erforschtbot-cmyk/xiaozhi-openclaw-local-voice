#!/usr/bin/env python3
"""Read-only prerequisites for a Jarvis host. Never prints secrets.

Prueft nur Vorbedingungen. Es installiert nichts, startet nichts und beweist
weder Provider-Login noch Audio, WLAN, Tokenuebereinstimmung oder physisches
Schalten. Exit 1 listet die fehlenden Punkte.

Portabel: Der Benutzerpfad wird NICHT vorausgesetzt. Ermittelt wird die
OpenClaw-Installation ueber OPENCLAW_NPM_ROOT oder `npm root -g`, damit ein
Host mit anderem Benutzernamen nicht faelschlich als kaputt gilt.
"""
import json
import shutil
import subprocess
from pathlib import Path

errors = []


def check(ok, label):
    print(("OK   " if ok else "FEHLT ") + label)
    if not ok:
        errors.append(label)


home = Path.home()
bridge = home / ".local/share/jarvis-realtime-bridge"
local_smarthome = home / ".local/share/alexa-direct"

print(f"Benutzer: {home}")
print(f"Bridge:   {bridge}")
print()

# --- Programme ------------------------------------------------------------
# docker wird nur zum Firmware-Bauen gebraucht; fehlt es, ist der Host fuer den
# Sprachbetrieb trotzdem vollstaendig. Deshalb separat und nicht als Fehler.
for command in ["node", "npm", "python3", "systemctl"]:
    check(shutil.which(command) is not None, "Programm: " + command)
if shutil.which("docker") is None:
    print("HINWEIS docker fehlt — nur zum Firmware-Bauen noetig, nicht fuer die Bridge")

# --- OpenClaw-Paket -------------------------------------------------------
npm_root = None
for candidate in (
    __import__("os").environ.get("OPENCLAW_NPM_ROOT"),
    str(home / ".npm-global/lib/node_modules"),
    (subprocess.run(["npm", "root", "-g"], capture_output=True, text=True).stdout.strip()
     if shutil.which("npm") else ""),
):
    if candidate and (Path(candidate) / "openclaw/package.json").is_file():
        npm_root = Path(candidate)
        break

if npm_root is None:
    check(False, "OpenClaw-Paket gefunden (OPENCLAW_NPM_ROOT oder npm root -g)")
else:
    package = npm_root / "openclaw/package.json"
    try:
        version = json.loads(package.read_text())["version"]
        check(True, f"OpenClaw-Paket: {package} (Version {version})")
        # Der dist-Patchsatz ist versionsgebunden; ein fehlender Satz ist kein
        # Fehler, aber die Info verhindert blindes Patchen.
        repo = Path(__file__).resolve().parents[1]
        if (repo / f"patches/openclaw-{version}").is_dir():
            print(f"INFO Patchsatz fuer {version} vorhanden")
        else:
            print(f"INFO Kein Patchsatz fuer {version} unter patches/ — "
                  "Schnellweg/Werkzeugpfad bleibt ungepatcht")
    except (OSError, ValueError, KeyError):
        check(False, "OpenClaw-Paket lesbar")

# --- Gateway-Konfiguration ------------------------------------------------
try:
    config = json.loads((home / ".openclaw/openclaw.json").read_text())
    agents = config.get("agents", {}).get("list", [])
    if not any(a.get("id") == "voice" for a in agents):
        print("INFO Sessionpraefix agent:voice ist kein statischer Agent-Eintrag; "
              "Zielaufloesung im Talk-Test pruefen.")
    if config.get("plugins", {}).get("entries", {}).get("iobroker-alexa", {}).get("enabled") is True:
        check(True, "ioBroker/Alexa-Plugin aktiviert")
    else:
        print("HINWEIS ioBroker/Alexa-Plugin nicht aktiviert — nur fuer Smart Home noetig")
except (OSError, ValueError, TypeError):
    check(False, "Lesbare OpenClaw-Konfiguration")

# --- TLS und Token --------------------------------------------------------
secrets = home / ".openclaw/secrets.json"
try:
    value = json.loads(secrets.read_text()).get("gateway", {}).get("authToken")
    check(isinstance(value, str) and bool(value.strip()), "Gateway-Token vorhanden (Wert bleibt verborgen)")
    check(secrets.stat().st_mode & 0o077 == 0, "Secrets-Datei nur fuer Besitzer zugaenglich")
except (OSError, ValueError, AttributeError):
    check(False, "Gateway-Token in lokaler secrets.json")
check((home / ".openclaw/ssl/gateway.crt").is_file(), "Gateway-CA fuer TLS vorhanden")

# --- Bridge-Dateien -------------------------------------------------------
# Vollstaendige Importkette. Fehlt eine Datei, startet die Bridge nicht oder
# der Gateway-Handler laedt nicht mehr — Datei fuer Datei pruefen.
required = [
    "server.py", "room_command.py", "piper_speaker.py",
    "openclaw-talk-realtime.mjs", "openclaw-thin-client.mjs",
    "local-voice.mjs", "local_stt.mjs",
    "voice-turn-context.mjs", "scoped-smart-home-router.mjs",
    "smart-home-router.mjs", "room-command.mjs", "room-router.mjs",
    "room-map.json", "disable-smart-home-fastpath",
    "refresh-alexa-catalog.mjs", "photo-consult.mjs",
]
for filename in required:
    check((bridge / filename).is_file(), "Bridge-Datei: " + filename)

screen = home / ".local/share/jarvis-screen"
for filename in ["show.py", "render.py", "serve.py", "palette.py",
                 "image-search.py", "templates/wetter.py", "templates/karte.py"]:
    check((screen / filename).is_file(), "Anzeige: " + filename)

display_skill = home / ".openclaw/workspace-voice/skills/display-cards/SKILL.md"
check(display_skill.is_file(), "Agent-Skill: display-cards")

if (screen / ".venv/bin/python").exists():
    result = subprocess.run(
        [str(screen / ".venv/bin/python"), "-c", "import PIL"],
        capture_output=True)
    check(result.returncode == 0, "Anzeige: Pillow importierbar")
else:
    check(False, "Anzeige: eigenes venv")

for filename in [
    "catalog.py", "control.py", "fuzzy.py", "localmap.py", "resolver.py",
    "rooms.py", "smarthome_cli.py", "panel.py", "overrides.py",
    "bridges.json", "rooms_overrides.json",
]:
    check((local_smarthome / filename).is_file(), "Lokaler Resolver: " + filename)

python = bridge / ".venv/bin/python"
if python.exists():
    result = subprocess.run(
        [str(python), "-c", "import opuslib, websockets"], capture_output=True)
    check(result.returncode == 0, "Python-Abhaengigkeiten der Bridge importierbar")
    # Piper ist optional (nur bei lokaler Stimme) — Existenz melden, nicht werten.
    local_tts = subprocess.run(
        [str(python), "-c", "import piper"], capture_output=True)
    print("INFO Piper im Bridge-venv " + ("vorhanden" if local_tts.returncode == 0 else "NICHT vorhanden (nur bei --tts-local noetig)"))
else:
    check(False, "Bridge-Python-venv")

# --- Dienste --------------------------------------------------------------
if shutil.which("systemctl"):
    for service in [
        "openclaw-gateway.service",
        "jarvis-realtime-bridge.service",
        "jarvis-screen.service",
        "wyoming-canary1b.service",
        "geraeteverwaltung.service",
    ]:
        result = subprocess.run(
            ["systemctl", "--user", "is-active", "--quiet", service],
            capture_output=True)
        check(result.returncode == 0, "Dienst aktiv: " + service)

for filename in ["panel.py", "overrides.py", "resolver.py", "control.py",
                 "hassil_router.py", "settings.py", "groups.py", "NOTICE.md"]:
    check((local_smarthome / filename).is_file(),
          "Geraeteverwaltung/Resolver: " + filename)
check((local_smarthome / "hassil-data" / "sentences" / "_common.yaml").is_file(),
      "HassIL-Satzvorlagen (hassil-data/sentences/_common.yaml)")

# Die zweite Stufe braucht hassil im Bridge-venv. Fehlt es, arbeitet der
# lokale Weg weiter - nur ohne Farbe und Farbtemperatur.
if python is not None:
    hat_hassil = subprocess.run(
        [str(python), "-c", "import hassil, yaml"], capture_output=True).returncode == 0
    if hat_hassil:
        check(True, "Python: hassil und pyyaml importierbar")
    else:
        print("HINWEIS hassil fehlt im Bridge-venv - zweite Stufe (Farbe,")
        print("        Farbtemperatur) bleibt aus. Nachinstallieren mit:")
        print("        pip install -r gateway/requirements.txt")

model_dir = home / ".cache/jarvis-stt-cand5-canary1b-onnx"
for filename in [
    "encoder-model.onnx", "encoder-model.onnx.data", "decoder-model.onnx",
    "vocab.txt", "config.json",
]:
    check((model_dir / filename).is_file(), "Canary-1B-Modell: " + filename)

print()
print("Nur Vorbedingungen geprueft; weder Provider-Login noch Audio, WLAN, "
      "Tokenuebereinstimmung oder physisches Schalten bewiesen.")
raise SystemExit(1 if errors else 0)
