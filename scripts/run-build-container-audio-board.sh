#!/usr/bin/env bash
# Fuehrt nur den Firmware-Compile im bereits gebauten Image aus.
set -euo pipefail
repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
output_dir="$repo_dir/build/firmware"
board_dir="waveshare/esp32-s3-audio-board"
board_name="esp32-s3-audio-board"
image="jarvis-audio-board-builder:idf61"
if docker info >/dev/null 2>&1; then docker_cmd=(docker)
elif sudo -n docker info >/dev/null 2>&1; then docker_cmd=(sudo -n docker)
else echo "Docker nicht erreichbar" >&2; exit 1; fi
mkdir -p "$output_dir"
cname="jarvis-audio-build-run"
"${docker_cmd[@]}" rm -f "$cname" >/dev/null 2>&1 || true
"${docker_cmd[@]}" create --name "$cname" \
  -e FIRMWARE_BOARD_DIR="$board_dir" \
  -e FIRMWARE_BOARD_NAME="$board_name" \
  -e FIRMWARE_LANGUAGE=de-DE \
  -e FIRMWARE_WAKE_WORD=wn9_jarvis_tts \
  -v "$output_dir:/output" \
  "$image" >/dev/null
"${docker_cmd[@]}" start -a "$cname"
rc=$?
"${docker_cmd[@]}" cp "$cname:/opt/xiaozhi-esp32/dependencies.lock" "$output_dir/dependencies.lock" 2>/dev/null || true
echo "CONTAINER_EXIT=$rc"
sha256sum "$output_dir"/*.bin 2>/dev/null || true
exit $rc
