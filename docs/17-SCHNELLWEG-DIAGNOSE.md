# 17 — Diagnose: lokaler Schaltweg, Oberfläche und Bildschirm-Dienst

Dieses Kapitel beantwortet eine häufige Fehldeutung nach einem Nachbau:

> „Die Geräteverwaltung (8088) und der Bildschirm-Dienst (8770) gehören zum
> Projekt, sind aber nicht Teil dessen, was ich gebaut habe.“

Das kann richtig sein – ist aber **kein Beleg dafür, dass der lokale
Schaltweg fehlt**. Die drei Dinge sind unabhängig.

## Drei getrennte Dinge

| Teil | Port | Rolle | Für schnelles Schalten nötig? |
|---|---|---|---|
| Lokaler Resolver (`smarthome_cli.py` + Python-venv) | – | löst „Licht Bad an“ ohne Agentenlauf auf und schreibt direkt | **ja** |
| Geräteverwaltung (`panel.py`) | 8088 | Browser-Oberfläche: Namen, Räume, Gruppen, Satz-Test | **nein** (nur zum Pflegen) |
| Bildschirm-Dienst (`serve.py`) | 8770 | liefert PNG/RGB565 an Boards **mit Display** | **nein** (Audio-Board hat kein Display) |

Die Oberfläche liegt **auf denselben JSON-Dateien** wie der Resolver. Man kann
den schnellen Weg vollständig nutzen, ohne 8088 je zu starten – und umgekehrt
eine laufende Oberfläche haben, während der schnelle Weg aus ist.

## Was der lokale Weg wirklich braucht

Der Node-Helfer (`gateway/local-voice.mjs`) ruft für jeden Satz einen eigenen
Python-Prozess auf. Diese drei Pfade sind **absolut** und nicht frei wählbar:

```text
~/.local/share/jarvis-realtime-bridge/.venv/bin/python
~/.local/share/alexa-direct/smarthome_cli.py
PYTHONPATH=~/.local/share/alexa-direct
```

Zusätzlich: Die Flag-Datei `~/.local/share/jarvis-realtime-bridge/disable-local-smarthome`
darf **nicht** existieren; sie schaltet den Weg absichtlich ab.

Das Python-venv muss `yaml` und `hassil` enthalten (stehen in
`gateway/requirements.txt`). Der Installer legt das venv an, kopiert den
Resolver und installiert diese Pakete.

## Prüfliste (read-only)

```bash
ls -l ~/.local/share/jarvis-realtime-bridge/.venv/bin/python
ls -l ~/.local/share/alexa-direct/smarthome_cli.py
~/.local/share/jarvis-realtime-bridge/.venv/bin/python -c 'import yaml, hassil; print("deps ok")'
test -e ~/.local/share/jarvis-realtime-bridge/disable-local-smarthome \
  && echo "FLAG LIEGT VOR – Weg absichtlich aus" \
  || echo "kein Flag"
```

## Funktionstest ohne Schalten

`smarthome_cli.py` kann auflösen, ohne zu schreiben:

```bash
SMARTHOME_DRY_RUN=1 ~/.local/share/jarvis-realtime-bridge/.venv/bin/python \
  ~/.local/share/alexa-direct/smarthome_cli.py \
  "schalte den Monitor aus"
```

Erwartet: ein JSON auf der letzten Zeile, z. B.

```json
{"ok": true, "device": "Monitor", "action": "set", "confirmation": "Monitor geschaltet.", …}
```

oder ein erklärender `reason` (`kein-geraet`, `frage`, `raum-mehrdeutig`,
`mehrere-aktionen`, …). **Es wird nichts geschaltet.** Das `confirmation`-Feld
im Dry-Run ist nur der Text, der *gesprochen würde*; es ist kein Schreibbeleg.

## Logzeilen richtig lesen

```bash
journalctl --user -u jarvis-realtime-bridge -n 100 --no-pager \
  | grep -i 'Lokaler Smart-Home-Weg'
```

| Zeile | Bedeutung |
|---|---|
| `Lokaler Smart-Home-Weg nicht nutzbar: …` | Aufruf konnte nicht starten: venv/CLI/PYTHONPATH fehlt |
| `… nicht aufgeloest (kein-geraet) -> Agent entscheidet` | Weg lief; Satz war nicht eindeutig, Agent übernimmt |
| `… Lokal geschaltet: Monitor (…)` | schneller Weg hat geschaltet |

„Nicht nutzbar“ ist damit **kein** Hinweis auf 8088 oder 8770, sondern auf die
Python-Seite des Resolvers.

## Behebung

```bash
# im Repo, nach git pull
./scripts/install-host.sh --public-host <LAN-IP-DES-OPENCLAW-HOSTS>
```

Der Installer kopiert den Resolver unabhängig von `--provider-stt` /
`--provider-voice`, legt das Bridge-venv neu an, installiert `yaml`/`hassil`,
schreibt die Units und startet Bridge, Bildschirm-Dienst und Geräteverwaltung.

Einzelne Teile bewusst weglassen:

```bash
# Oberfläche nicht starten (schneller Weg bleibt unberührt)
systemctl --user disable --now geraeteverwaltung.service

# Bildschirm-Dienst auf einem Board ohne Display abschalten
systemctl --user disable --now jarvis-screen.service
```

## Wenn 8088 trotzdem geprüft werden soll

```bash
systemctl --user is-active geraeteverwaltung.service
ss -ltnp | grep ':8088'
curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8088/
curl -sS -o /dev/null -w '%{http_code}\n' http://<LAN-IP-DES-OPENCLAW-HOSTS>:8088/
```

Erwartet: `active`, ein LISTEN-Eintrag und zweimal HTTP `200`.
Aufbau, Port/Host, Sicherheit und Fehlersuche der Seite stehen in
[`16-WEBOBERFLAECHE.md`](16-WEBOBERFLAECHE.md).

## Rückmeldung nach der Prüfung

Sinnvoll sind genau diese Angaben, damit der Zustand vergleichbar ist:

1. Commit-Hash des Repos (`git rev-parse HEAD`);
2. Status von `jarvis-realtime-bridge`, `geraeteverwaltung`, `jarvis-screen`;
3. HTTP-Code von `:8088` (falls betrieben);
4. das Dry-Run-JSON aus dem Funktionstest;
5. die letzten Zeilen zu `Lokaler Smart-Home-Weg` aus dem Bridge-Log.

## Voraussetzung, die nicht verhandelbar ist

Der Patch enthält absolute Pfade unter `/home/openclaw`. Bridge und Resolver
lassen sich dagegen über `JARVIS_DATA_ROOT`/`JARVIS_SMARTHOME_DIR` umstellen.
Konfiguration, Container-Betrieb und die Fragen an den Betreiber:
[`18-ANDERE-HOSTS.md`](18-ANDERE-HOSTS.md).

Der Patch und der Helfer enthalten absolute Pfade unter `/home/openclaw`.
Ein anderer Benutzername bricht sie still oder beim Laden. Wer den Host anders
aufsetzt, muss den Patchsatz neu erzeugen und die Pfade im Helfer anpassen –
das ist kein Konfigurationsdetail, sondern Teil der Schnittstelle.
