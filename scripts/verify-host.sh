#!/usr/bin/env bash
set -euo pipefail

# Prueft Host, Bridge und — falls aktiviert — die lokale Sprachausgabe.

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "=== Syntax ==="
python3 -m py_compile "$repo_dir/gateway/server.py" "$repo_dir/gateway/piper_speaker.py"
bash -n "$repo_dir/scripts/install-host.sh"
node --check "$repo_dir/gateway/openclaw-talk-realtime.mjs"

echo
echo "=== Dienste ==="
systemctl --user is-active openclaw-gateway.service
systemctl --user is-active jarvis-realtime-bridge.service

echo
echo "=== Ports ==="
ss -ltn | grep -E ':8765|:8766'

echo
echo "=== Lokale Sprachausgabe ==="
unit="$HOME/.config/systemd/user/jarvis-realtime-bridge.service"
if grep -q -- '--tts-local' "$unit" 2>/dev/null; then
  install_dir="$(systemctl --user show jarvis-realtime-bridge.service -p WorkingDirectory --value)"
  voice="$(grep -oP -- '--tts-voice \K[^ ]+' "$unit" | head -1)"
  voice_dir="$(grep -oP -- '--tts-voice-dir \K[^ ]+' "$unit" | head -1)"
  echo "Lokale Stimme aktiv: ${voice:-?} in ${voice_dir:-?}"
  if [[ -n "$voice_dir" && -n "$voice" && ! -f "$voice_dir/$voice.onnx" ]]; then
    echo "FEHLER: Stimme $voice_dir/$voice.onnx fehlt" >&2
    exit 1
  fi
  # Das Piper-Modul muss im Bridge-Venv importierbar sein.
  "$install_dir/.venv/bin/python" - <<'PY'
import piper  # noqa: F401
print("Piper-Modul im Bridge-Venv importierbar")
PY
else
  echo "Provider-Stimme aktiv (keine lokale Sprachausgabe)"
fi

echo
echo "Host verification passed"
