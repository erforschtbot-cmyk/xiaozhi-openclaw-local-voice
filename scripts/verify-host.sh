#!/usr/bin/env bash
set -euo pipefail

# Prueft Host, Bridge und — falls aktiviert — die lokale Sprachkette.

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "=== Syntax ==="
python3 -m py_compile "$repo_dir/gateway/server.py" "$repo_dir/gateway/piper_speaker.py"
bash -n "$repo_dir/scripts/install-host.sh"
node --check "$repo_dir/gateway/openclaw-talk-realtime.mjs"
node --check "$repo_dir/gateway/local_stt.mjs"
node --check "$repo_dir/gateway/local-voice.mjs"

echo
echo "=== Dienste ==="
systemctl --user is-active openclaw-gateway.service
systemctl --user is-active jarvis-realtime-bridge.service

echo
echo "=== Ports ==="
ss -ltn | grep -E ':8765|:8766'

echo
echo "=== Lokale Sprachkette ==="
unit="$HOME/.config/systemd/user/jarvis-realtime-bridge.service"
install_dir="$(systemctl --user show jarvis-realtime-bridge.service -p WorkingDirectory --value)"

if grep -q -- '--stt-local' "$unit" 2>/dev/null; then
  stt_uri="$(grep -oP -- '--stt-uri \K[^ ]+' "$unit" | head -1)"
  echo "Lokale Erkennung aktiv: Whisper unter ${stt_uri:-?}"
  # Der Dienst muss antworten und das erwartete Modell melden.
  "$install_dir/.venv/bin/python" - "$stt_uri" <<'PY'
import asyncio, sys
from wyoming.client import AsyncTcpClient
from wyoming.info import Describe, Info

uri = sys.argv[1]
host, port = uri.split(":")
port = int(port)

async def main() -> int:
    client = AsyncTcpClient(host, port)
    await client.connect()
    await client.write_event(Describe().event())
    event = await asyncio.wait_for(client.read_event(), timeout=5)
    info = Info.from_event(event)
    models = [m.name for a in info.asr for m in a.models]
    print(f"  Whisper erreichbar, Modell(e): {models}")
    await client.disconnect()
    return 0

sys.exit(asyncio.run(main()))
PY
else
  echo "Erkennung: Provider (nicht lokal)"
fi

if grep -q -- '--tts-local' "$unit" 2>/dev/null; then
  voice="$(grep -oP -- '--tts-voice \K[^ ]+' "$unit" | head -1)"
  voice_dir="$(grep -oP -- '--tts-voice-dir \K[^ ]+' "$unit" | head -1)"
  echo "Lokale Sprachausgabe aktiv: ${voice:-?} in ${voice_dir:-?}"
  if [[ -n "$voice_dir" && -n "$voice" && ! -f "$voice_dir/$voice.onnx" ]]; then
    # Stimmen liegen haeufig unter <dir>/de/de_DE/<name>.onnx
    if ! find "$voice_dir" -name "$voice.onnx" -print -quit | grep -q .; then
      echo "FEHLER: Stimme $voice nicht unter $voice_dir gefunden" >&2
      exit 1
    fi
  fi
  "$install_dir/.venv/bin/python" - <<'PY'
import piper  # noqa: F401
print("  Piper-Modul im Bridge-Venv importierbar")
PY
else
  echo "Sprachausgabe: Provider"
fi

echo
echo "Host verification passed"
