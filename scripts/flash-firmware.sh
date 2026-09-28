#!/usr/bin/env bash
set -euo pipefail

usage() {
  echo "Usage: $0 --port /dev/ttyACM0 [--full-image-i-understand]" >&2
  exit 2
}

port=""
full=0
while (($#)); do
  case "$1" in
    --port) port="${2:-}"; shift 2 ;;
    --full-image-i-understand) full=1; shift ;;
    *) usage ;;
  esac
done
[[ -n "$port" && -e "$port" ]] || { echo "Serial port missing: $port" >&2; exit 1; }

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_dir"
venv_dir="$repo_dir/.venv-esptool"
python3 -m venv "$venv_dir"
"$venv_dir/bin/python" -m pip install --quiet 'esptool==5.4.0'
esptool="$venv_dir/bin/esptool"

if ((full)); then
  image="$repo_dir/firmware/prebuilt/merged-binary.bin"
  sha256sum --check <(grep 'firmware/prebuilt/merged-binary.bin$' "$repo_dir/CHECKSUMS.sha256")
  "$esptool" --chip esp32s3 --port "$port" write-flash 0x0 "$image"
  "$esptool" --chip esp32s3 --port "$port" verify-flash 0x0 "$image"
else
  image="$repo_dir/firmware/prebuilt/xiaozhi.bin"
  sha256sum --check <(grep 'firmware/prebuilt/xiaozhi.bin$' "$repo_dir/CHECKSUMS.sha256")
  "$esptool" --chip esp32s3 --port "$port" write-flash 0x20000 "$image"
  "$esptool" --chip esp32s3 --port "$port" verify-flash 0x20000 "$image"
fi
echo "Flash and verification completed"
