# Lokaler Smart-Home-Schnellweg

Dieser Quellbaum ist eine harte Laufzeitabhängigkeit von
`gateway/local-voice.mjs`. Eindeutige Schaltbefehle werden lokal aufgelöst und
über ioBroker Simple API ausgeführt, bevor ein Agentenlauf entsteht.

## Installation

`scripts/install-host.sh` kopiert die Quellen nach
`~/.local/share/alexa-direct/`. Der ioBroker-Endpunkt kann gesetzt werden:

```bash
export IOBROKER_SIMPLE_API=http://IOBROKER-IP:8087
```

Ohne Variable gilt der Referenzwert `http://192.168.178.24:8087`. Die Dateien
`catalog.json`, `localmap.json` und `rooms.json` werden aus ioBroker
erzeugt und sind deshalb nicht versioniert. `bridges.json` und
`rooms_overrides.json` sind dagegen gepflegte Konfiguration.

## Erstaufbau und Prüfung

```bash
cd ~/.local/share/alexa-direct
python catalog.py
python localmap.py
python rooms.py
python test_resolver.py
SMARTHOME_DRY_RUN=1 python smarthome_cli.py "Monitor an"
```

Der letzte Befehl darf kein Gerät schalten. Für einen anderen Haushalt müssen
die beiden gepflegten JSON-Dateien angepasst werden; unbekannte oder mehrdeutige
Sätze fallen sicher auf den normalen Agentenweg zurück.

Mit `touch ~/.local/share/jarvis-realtime-bridge/disable-local-smarthome` wird
dieser Schnellweg vollständig deaktiviert.
