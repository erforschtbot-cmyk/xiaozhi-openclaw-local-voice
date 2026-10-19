#!/usr/bin/env python3
"""Read-only prerequisites for the documented Jarvis reference host. Never prints secrets."""
import json
import os
from pathlib import Path
import shutil
import subprocess

errors = []
def check(ok, label):
    print(('OK   ' if ok else 'FEHLT ') + label)
    if not ok:
        errors.append(label)

home = Path.home()
bridge = home / '.local/share/jarvis-realtime-bridge'
check(str(home) == '/home/openclaw', 'Referenz-Benutzerpfad /home/openclaw')
for command in ['node', 'npm', 'python3', 'git', 'systemctl', 'docker']:
    check(shutil.which(command) is not None, 'Programm: ' + command)
package = home / '.npm-global/lib/node_modules/openclaw/package.json'
try:
    check(json.loads(package.read_text())['version'] == '2026.10.4', 'OpenClaw-Version 2026.10.4 am Referenzpfad')
except (OSError, ValueError, KeyError):
    check(False, 'OpenClaw-Paket am Referenzpfad')
try:
    config = json.loads((home / '.openclaw/openclaw.json').read_text())
    agents = config.get('agents', {}).get('list', [])
    if not any(a.get('id') == 'voice' for a in agents):
        print('INFO Sessionpräfix agent:voice ist kein statischer Agent-Eintrag; Zielauflösung im Talk-Test prüfen.')
    check(config.get('plugins', {}).get('entries', {}).get('iobroker-alexa', {}).get('enabled') is True,
          'ioBroker/Alexa-Plugin aktiviert (nur für Smart Home erforderlich)')
except (OSError, ValueError, TypeError):
    check(False, 'Lesbare OpenClaw-Konfiguration')
secrets = home / '.openclaw/secrets.json'
try:
    value = json.loads(secrets.read_text()).get('gateway', {}).get('authToken')
    check(isinstance(value, str) and bool(value.strip()), 'Gateway-Token vorhanden (Wert bleibt verborgen)')
    check(secrets.stat().st_mode & 0o077 == 0, 'Secrets-Datei nur für Besitzer zugänglich')
except (OSError, ValueError, AttributeError):
    check(False, 'Gateway-Token in lokaler secrets.json')
check((home / '.openclaw/ssl/gateway.crt').is_file(), 'Gateway-CA für TLS vorhanden')
required = ['server.py', 'room_command.py', 'openclaw-talk-realtime.mjs',
            'voice-turn-context.mjs', 'scoped-smart-home-router.mjs',
            'smart-home-router.mjs', 'room-command.mjs', 'room-map.json',
            'disable-smart-home-fastpath', 'refresh-alexa-catalog.mjs']
for filename in required:
    check((bridge / filename).is_file(), 'Bridge-Datei: ' + filename)
python = bridge / '.venv/bin/python'
if python.exists():
    result = subprocess.run([str(python), '-c', 'import opuslib, websockets'], capture_output=True)
    check(result.returncode == 0, 'Python-Abhängigkeiten der Bridge importierbar')
else:
    check(False, 'Bridge-Python-venv')
if shutil.which('systemctl'):
    for service in ['openclaw-gateway.service', 'jarvis-realtime-bridge.service']:
        result = subprocess.run(['systemctl', '--user', 'is-active', '--quiet', service], capture_output=True)
        check(result.returncode == 0, 'Dienst aktiv: ' + service)
print('\nNur Vorbedingungen geprüft; weder Provider-Login noch Audio, WLAN, Tokenübereinstimmung oder physisches Schalten bewiesen.')
raise SystemExit(1 if errors else 0)
