#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "Usage: $0 --public-host LAN_IP [--install-dir PATH] [--tts-voice NAME] [--tts-voice-dir PATH]" >&2
  echo "       --stt-uri HOST:PORT  Adresse des lokalen Whisper-Dienstes (Standard 127.0.0.1:10300)" >&2
  echo "       --provider-stt       installiert ohne lokale Erkennung (OpenAI hoert zu)" >&2
  echo "       --provider-voice     installiert ohne lokale Sprachausgabe (OpenAI-Stimme)" >&2
  exit 2
}

public_host=""
tts_voice="de_DE-kerstin-low"
tts_voice_dir="$HOME/.local/share/piper-voices"
stt_uri="127.0.0.1:10300"
local_voice=1
local_stt=1
install_dir="${XDG_DATA_HOME:-$HOME/.local/share}/jarvis-realtime-bridge"
while (($#)); do
  case "$1" in
    --public-host) public_host="${2:-}"; shift 2 ;;
    --install-dir) install_dir="${2:-}"; shift 2 ;;
    --tts-voice) tts_voice="${2:-}"; shift 2 ;;
    --tts-voice-dir) tts_voice_dir="${2:-}"; shift 2 ;;
    --stt-uri) stt_uri="${2:-}"; shift 2 ;;
    --provider-stt) local_stt=0; shift ;;
    --provider-voice) local_voice=0; shift ;;
    *) usage ;;
  esac
done

# Lokale Erkennung setzt lokale Sprachausgabe voraus: der lokale Helfer
# liefert kein Audio; ohne Piper gäbe es nichts zu hören.
if (( local_stt )); then
  local_voice=1
fi
[[ -n "$public_host" ]] || usage

for command_name in python3 node systemctl; do
  command -v "$command_name" >/dev/null || { echo "Missing command: $command_name" >&2; exit 1; }
done

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
mkdir -p "$install_dir" "$HOME/.config/systemd/user"
install -m 0644 "$repo_dir/gateway/server.py" "$install_dir/server.py"
install -m 0644 "$repo_dir/gateway/piper_speaker.py" "$install_dir/piper_speaker.py"
install -m 0644 "$repo_dir/gateway/local_stt.mjs" "$install_dir/local_stt.mjs"
install -m 0755 "$repo_dir/gateway/local-voice.mjs" "$install_dir/local-voice.mjs"
install -m 0755 "$repo_dir/gateway/openclaw-talk-realtime.mjs" "$install_dir/openclaw-talk-realtime.mjs"
install -m 0644 "$repo_dir/gateway/requirements.txt" "$install_dir/requirements.txt"

python3 -m venv "$install_dir/.venv"
"$install_dir/.venv/bin/python" -m pip install --upgrade pip
"$install_dir/.venv/bin/python" -m pip install -r "$install_dir/requirements.txt"

if (( local_voice )); then
  # Piper in dasselbe Venv installieren, in dem die Bridge laeuft. So findet
  # der Bridge-Prozess das Modul ohne zusaetzliche PYTHONPATH-Tricks.
  "$install_dir/.venv/bin/python" -m pip install -r "$repo_dir/gateway/requirements-tts.txt"
  if [[ ! -f "$tts_voice_dir/$tts_voice.onnx" ]]; then
    echo "Hinweis: Stimme $tts_voice nicht unter $tts_voice_dir gefunden." >&2
    echo "        Stimmen holen:  python3 -m piper.download_voices $tts_voice" >&2
    echo "        oder Paket piper-voices-common der Distribution verwenden." >&2
  fi
fi

unit_target="$HOME/.config/systemd/user/jarvis-realtime-bridge.service"
python3 - "$repo_dir/systemd/jarvis-realtime-bridge.service.in" "$unit_target" \
  "$install_dir" "$public_host" "$tts_voice" "$tts_voice_dir" "$stt_uri" \
  "$local_voice" "$local_stt" <<'PY'
from pathlib import Path
import sys
(
    source, target, install_dir, public_host, tts_voice,
    tts_voice_dir, stt_uri, local_voice, local_stt,
) = sys.argv[1:]
text = Path(source).read_text()
text = (
    text.replace("@INSTALL_DIR@", install_dir)
    .replace("@PUBLIC_HOST@", public_host)
    .replace("@TTS_VOICE@", tts_voice)
    .replace("@TTS_VOICE_DIR@", tts_voice_dir)
    .replace("@STT_URI@", stt_uri)
)
# Provider-Varianten: die jeweiligen lokalen Schalter entfernen.
if local_stt == "0":
    text = text.replace(f" --stt-local --stt-uri {stt_uri} --stt-language de", "")
if local_voice == "0":
    text = text.replace(
        f" --tts-local --tts-voice {tts_voice} --tts-voice-dir {tts_voice_dir}",
        "",
    )
Path(target).write_text(text)
PY

systemctl --user daemon-reload
systemctl --user enable --now jarvis-realtime-bridge.service
systemctl --user is-active --quiet jarvis-realtime-bridge.service
if (( local_stt )); then
  echo "Installed and active: jarvis-realtime-bridge.service"
  echo "  recognition: local Whisper at $stt_uri"
else
  echo "Installed and active: jarvis-realtime-bridge.service (recognition: provider)"
fi
if (( local_voice )); then
  echo "  voice:       local Piper ($tts_voice)"
else
  echo "  voice:       provider"
fi
