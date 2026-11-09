#!/usr/bin/env bash
# Installiert den lokalen Canary-1B-v2-Wyoming-Dienst vollständig.
set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
canary_dir="${CANARY_DIR:-$HOME/.local/share/wyoming-canary1b}"
canary_venv="${CANARY_VENV:-$HOME/.local/share/venvs/jarvis-canary1b}"
model_dir="${CANARY_MODEL_DIR:-$HOME/.cache/jarvis-stt-cand5-canary1b-onnx}"
port="${CANARY_PORT:-10303}"
start=1
[[ "${1:-}" == "--no-start" ]] && start=0

for command_name in python3 curl systemctl; do
  command -v "$command_name" >/dev/null || { echo "FEHLT: $command_name" >&2; exit 1; }
done
mkdir -p "$canary_dir" "$(dirname "$canary_venv")" "$model_dir" "$HOME/.config/systemd/user"
python3 -m venv "$canary_venv"
"$canary_venv/bin/python" -m pip install --upgrade pip
"$canary_venv/bin/python" -m pip install -r "$repo_dir/gateway/requirements-canary1b.txt"
install -m 0755 "$repo_dir/gateway/wyoming_canary1b.py" "$canary_dir/wyoming_canary1b.py"

if [[ ! -s "$canary_dir/silero_vad.onnx" ]]; then
  curl --fail --location --retry 3 \
    https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/silero_vad.onnx \
    --output "$canary_dir/silero_vad.onnx"
fi

# Der erste load_model-Aufruf lädt den gepinnten ONNX-Satz. Warmup belegt,
# dass nicht nur Dateien vorhanden sind, sondern der Decoder wirklich lädt.
HF_HUB_OFFLINE=0 "$canary_venv/bin/python" - "$model_dir" <<'PY'
import sys
import numpy as np
import onnx_asr
model = onnx_asr.load_model("nemo-canary-1b-v2", path=sys.argv[1], quantization="int8")
model.recognize(np.zeros(4000, dtype=np.float32), sample_rate=16000, language="de")
print("Canary-1B-v2 Modell geladen und Warmup erfolgreich")
PY

python3 - "$repo_dir/systemd/wyoming-canary1b.service.in" \
  "$HOME/.config/systemd/user/wyoming-canary1b.service" \
  "$canary_dir" "$canary_venv" "$model_dir" "$port" <<'PY'
from pathlib import Path
import sys
source, target, canary_dir, canary_venv, model_dir, port = sys.argv[1:]
text = (Path(source).read_text()
        .replace("@CANARY_DIR@", canary_dir)
        .replace("@CANARY_VENV@", canary_venv)
        .replace("@MODEL_DIR@", model_dir)
        .replace("@CANARY_PORT@", port))
Path(target).write_text(text)
PY

if (( start )); then
  systemctl --user daemon-reload
  systemctl --user enable --now wyoming-canary1b.service
  systemctl --user is-active --quiet wyoming-canary1b.service
fi
echo "Canary-1B-v2 installiert: tcp://127.0.0.1:$port"
