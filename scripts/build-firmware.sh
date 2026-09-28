#!/usr/bin/env bash
set -euo pipefail

usage() { echo "Usage: $0 --public-host LAN_IP" >&2; exit 2; }
public_host=""
while (($#)); do
  case "$1" in
    --public-host) public_host="${2:-}"; shift 2 ;;
    *) usage ;;
  esac
done
[[ -n "$public_host" ]] || usage
command -v git >/dev/null || { echo "git missing" >&2; exit 1; }
command -v docker >/dev/null || { echo "docker missing" >&2; exit 1; }
if docker info >/dev/null 2>&1; then
  docker_cmd=(docker)
elif sudo -n docker info >/dev/null 2>&1; then
  docker_cmd=(sudo -n docker)
else
  echo "Docker daemon is not accessible (directly or via passwordless sudo)" >&2
  exit 1
fi

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source_dir="$repo_dir/source/xiaozhi-esp32"
output_dir="$repo_dir/build/firmware"
rm -rf "$source_dir"
mkdir -p "$(dirname "$source_dir")" "$output_dir"

git clone --filter=blob:none https://github.com/78/xiaozhi-esp32.git "$source_dir"
git -C "$source_dir" checkout 4632dc51f0a5ad26e08542e131e6e48da41e4ff3
git -C "$source_dir" apply --check "$repo_dir/firmware/patches/xiaozhi-esp32-openclaw.patch"
git -C "$source_dir" apply "$repo_dir/firmware/patches/xiaozhi-esp32-openclaw.patch"

git clone https://github.com/78/uart-uhci.git "$source_dir/local_components/uart-uhci"
git -C "$source_dir/local_components/uart-uhci" checkout 6ea5f576af84640209aa6b98e27be92b8ce418c5
git -C "$source_dir/local_components/uart-uhci" apply --check "$repo_dir/firmware/patches/uart-uhci-idf61.patch"
git -C "$source_dir/local_components/uart-uhci" apply "$repo_dir/firmware/patches/uart-uhci-idf61.patch"
if [[ -f "$repo_dir/firmware/dependencies.lock" ]]; then
  install -m 0644 "$repo_dir/firmware/dependencies.lock" "$source_dir/dependencies.lock"
fi

python3 - "$source_dir/main/boards/waveshare/esp32-s3-touch-lcd-4b/config.json" "$public_host" <<'PY'
from pathlib import Path
import sys
path = Path(sys.argv[1])
text = path.read_text().replace("192.168.178.143", sys.argv[2])
path.write_text(text)
PY

image="xiaozhi-openclaw-builder:esp-idf61"
"${docker_cmd[@]}" build \
  --build-arg IDF_IMAGE=espressif/idf:v6.1 \
  --build-arg FIRMWARE_SOURCE_REVISION=4632dc51f0a5ad26e08542e131e6e48da41e4ff3-openclaw \
  -f "$source_dir/docker/firmware-builder/Dockerfile" \
  -t "$image" "$source_dir"

container_name="xiaozhi-openclaw-build-$$"
cleanup_container() {
  "${docker_cmd[@]}" rm -f "$container_name" >/dev/null 2>&1 || true
}
trap cleanup_container EXIT
"${docker_cmd[@]}" create --name "$container_name" \
  -e FIRMWARE_BOARD_DIR=waveshare/esp32-s3-touch-lcd-4b \
  -e FIRMWARE_BOARD_NAME=esp32-s3-touch-lcd-4b \
  -e FIRMWARE_LANGUAGE=de-DE \
  -e FIRMWARE_WAKE_WORD=wn9_jarvis_tts \
  -v "$output_dir:/output" \
  "$image" >/dev/null
"${docker_cmd[@]}" start -a "$container_name"
"${docker_cmd[@]}" cp "$container_name:/opt/xiaozhi-esp32/dependencies.lock" \
  "$output_dir/dependencies.lock"
cleanup_container
trap - EXIT

sha256sum "$output_dir"/*.bin
