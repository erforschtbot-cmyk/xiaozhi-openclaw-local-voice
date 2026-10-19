# ioBroker/Alexa-Werkzeug-Plugin

Am 20.10.2026 aus dem aktiven Plugin übernommen: TypeScript-Quellen, Tests,
Lockdatei und ausgelieferter JavaScript-Code. Keine node_modules oder Secrets.
Die bisher fehlende Agent-Werkzeugseite ergänzt den Gateway-Schnellweg.

## Bauen

Voraussetzung: OpenClaw 2026.10.4 unter
`/home/openclaw/.npm-global/lib/node_modules/openclaw`. package.json/Lockfile
verweisen per `file:` darauf. Andere npm-Pfade erfordern eine angepasste
Abhängigkeit und neu geprüfte Lockdatei.

```bash
cd plugins/iobroker-alexa
npm ci
npm run build
npm test
```

Vor dem Einsatz `ENDPOINT` in `src/index.ts` auf den eigenen ioBroker-simple-api-
Host und Echo-textCommand-Datenpunkt setzen, danach erneut bauen.
HTTP-Erfolg bedeutet angenommen, nicht physisch geschaltet. Keine Auto-Retries.

## Einbinden

Projekt samt dist, Manifest, package.json und installierten Dependencies an einem
dauerhaften Ort installieren (Referenz:
`/home/openclaw/.local/share/iobroker-alexa-plugin`). Folgende Felder zur bestehenden
OpenClaw-Konfiguration **hinzufügen**, vorhandene Listen/Einträge erhalten:

```json
{
  "plugins": {
    "load": { "paths": ["/home/openclaw/.local/share/iobroker-alexa-plugin"] },
    "entries": { "iobroker-alexa": { "enabled": true } }
  }
}
```

Etwaige Plugin-Allowlisten und Tool-Policies müssen das Plugin und seine Tools
zulassen. `openclaw config validate` ausführen. Einen nötigen Gateway-Neustart
führt der Betreiber aus. Im Voice-Agenten Verfügbarkeit von `iobroker_alexa_send`
und `iobroker_alexa` prüfen. Eine Schaltprobe erfordert ein bewusst ausgewähltes
Gerät; das Repository-Audit hat nichts geschaltet.

Die Raumzuordnung benutzt AsyncLocalStorage aus dem Gateway-Patch
(`Symbol.for("jarvis.voiceTurn.v1")`). Ohne eindeutigen Kontext muss der Raum
im Auftrag stehen. Bloßes Kopieren dieses Plugins reicht daher für raumabhängiges
„Licht an“ an mehreren Geräten nicht aus.
