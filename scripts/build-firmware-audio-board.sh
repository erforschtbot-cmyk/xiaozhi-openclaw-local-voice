#!/usr/bin/env bash
# Baut die Jarvis-Firmware fuer das Waveshare ESP32-S3-AUDIO-Board.
# Gepinnter Upstream + Board-Patch (led-Ring) + deutscher Sprache.
set -euo pipefail

public_host="${1:-192.168.178.143}"
board_dir="waveshare/esp32-s3-audio-board"
board_name="esp32-s3-audio-board"
wake_word="${2:-wn9_jarvis_tts}"

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source_dir="$repo_dir/source/xiaozhi-esp32"
output_dir="$repo_dir/build/firmware"

if docker info >/dev/null 2>&1; then docker_cmd=(docker)
elif sudo -n docker info >/dev/null 2>&1; then docker_cmd=(sudo -n docker)
else echo "Docker nicht erreichbar" >&2; exit 1; fi

rm -rf "$source_dir"; mkdir -p "$(dirname "$source_dir")" "$output_dir"
git clone --filter=blob:none https://github.com/78/xiaozhi-esp32.git "$source_dir"
git -C "$source_dir" checkout 4632dc51f0a5ad26e08542e131e6e48da41e4ff3
git -C "$source_dir" apply --check "$repo_dir/firmware/patches/jarvis-audio-board-ring.patch"
git -C "$source_dir" apply        "$repo_dir/firmware/patches/jarvis-audio-board-ring.patch"

# WLAN/Host in die Board-Konfiguration eintragen
python3 - "$source_dir/main/boards/$board_dir/config.json" "$public_host" <<'PY'
from pathlib import Path
import sys
p = Path(sys.argv[1]); p.write_text(p.read_text().replace("192.168.178.143", sys.argv[2]))
PY

image="jarvis-audio-board-builder:idf61"
"${docker_cmd[@]}" build \
  --build-arg IDF_IMAGE=espressif/idf:v6.1 \
  --build-arg FIRMWARE_SOURCE_REVISION=4632dc51f0a5ad26e08542e131e6e48da41e4ff3-jarvis-ring \
  -f "$source_dir/docker/firmware-builder/Dockerfile" \
  -t "$image" "$source_dir"

cname="jarvis-audio-board-build-$$"
trap '"${docker_cmd[@]}" rm -f "$cname" >/dev/null 2>&1 || true' EXIT
"${docker_cmd[@]}" create --name "$cname" \
  -e FIRMWARE_BOARD_DIR="$board_dir" \
  -e FIRMWARE_BOARD_NAME="$board_name" \
  -e FIRMWARE_LANGUAGE=de-DE \
  -e FIRMWARE_WAKE_WORD="$wake_word" \
  -v "$output_dir:/output" \
  "$image" >/dev/null
"${docker_cmd[@]}" start -a "$cname"
"${docker_cmd[@]}" cp "$cname:/opt/xiaozhi-esp32/dependencies.lock" "$output_dir/dependencies.lock"
trap - EXIT
"${docker_cmd[@]}" rm -f "$cname" >/dev/null 2>&1 || true
sha256sum "$output_dir"/*.bin
