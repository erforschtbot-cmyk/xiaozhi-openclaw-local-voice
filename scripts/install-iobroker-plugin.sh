#!/usr/bin/env bash
# Baut und aktiviert das optionale Agent-Werkzeug-Plugin ohne Gateway-Neustart.
set -euo pipefail
endpoint="${1:-}"
[[ "$endpoint" =~ ^https?://.+\?value=$ ]] || {
  echo "Usage: $0 'http://IOBROKER:8087/set/alexa2.0.Echo-Devices.ID.Commands.textCommand?value='" >&2
  exit 2
}
repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
src="$repo_dir/plugins/iobroker-alexa"
target="${XDG_DATA_HOME:-$HOME/.local/share}/iobroker-alexa-plugin"
config="$HOME/.openclaw/openclaw.json"
[[ -f "$config" ]] || { echo "FEHLT: $config" >&2; exit 1; }

(cd "$src" && npm ci && npm run build && npm test)
mkdir -p "$target"
for file in README.md openclaw.plugin.json package.json package-lock.json; do
  install -m 0644 "$src/$file" "$target/$file"
done
mkdir -p "$target/dist"
install -m 0644 "$src/dist/index.js" "$src/dist/index.d.ts" "$target/dist/"
(cd "$target" && npm ci --omit=dev)

python3 - "$config" "$target" "$endpoint" <<'PY'
from pathlib import Path
import json, os, shutil, sys, time
path, target, endpoint = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
data = json.loads(path.read_text())
plugins = data.setdefault("plugins", {})
paths = plugins.setdefault("load", {}).setdefault("paths", [])
if target not in paths:
    paths.append(target)
entry = plugins.setdefault("entries", {}).setdefault("iobroker-alexa", {})
entry["enabled"] = True
entry["config"] = {
    "endpoint": endpoint,
    "activeRoomPath": str(Path.home() / ".local/share/jarvis-realtime-bridge/active-room.json"),
}
backup = path.with_name(path.name + ".bak-jarvis-plugin-" + time.strftime("%Y%m%d-%H%M%S"))
shutil.copy2(path, backup)
tmp = path.with_suffix(path.suffix + ".tmp")
tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
os.replace(tmp, path)
print(f"OpenClaw-Konfiguration aktualisiert; Backup: {backup}")
PY

openclaw config validate
echo "Plugin installiert und aktiviert. Ein Gateway-Neustart ist Betreiberaufgabe."
