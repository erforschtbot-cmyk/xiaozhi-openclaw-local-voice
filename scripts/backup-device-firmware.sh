#!/usr/bin/env bash
set -euo pipefail

# Liest die KOMPLETTE Firmware vom Geraet in eine lokale Sicherung.
#
# Reiner Lesevorgang: es wird nichts geschrieben oder geloescht. Zweck ist der
# Rueckweg vor jedem Flashen.
#
# WICHTIG — Zugangsdaten
# ----------------------
# Ein Voll-Abbild enthaelt den NVS-Bereich und damit die WLAN-Zugangsdaten des
# Geraets. Es gehoert NIEMALS in ein Repository oder auf einen oeffentlichen
# Speicher. Das Zielverzeichnis liegt bewusst ausserhalb des Repos.
#
# Aufruf:
#   scripts/backup-device-firmware.sh [--port /dev/ttyACM0] [--out-dir PFAD]

port="/dev/ttyACM0"
out_root="$HOME/device-backups/jarvis-xiaozhi"
# Hohe Baudrate: der Standard 115200 braucht fuer 16 MB rund 23 Minuten und
# wird dadurch anfaellig fuer Abbrueche. Der Stub-Flasher schafft 921600.
baud="921600"

usage() {
  echo "Usage: $0 [--port /dev/ttyACM0] [--out-dir PFAD] [--baud N]" >&2
  exit 2
}

while (($#)); do
  case "$1" in
    --port) port="${2:-}"; shift 2 ;;
    --out-dir) out_root="${2:-}"; shift 2 ;;
    --baud) baud="${2:-}"; shift 2 ;;
    *) usage ;;
  esac
done

[[ -e "$port" ]] || { echo "Serieller Port fehlt: $port" >&2; exit 1; }

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
stamp="$(date +%Y%m%d-%H%M%S)"
out_dir="$out_root/$stamp"
mkdir -p "$out_dir"

venv="$HOME/.openclaw/workspace-allgemein/.tmp/jarvis-esptool-venv"
if [[ ! -x "$venv/bin/esptool" ]]; then
  mkdir -p "$(dirname "$venv")"
  python3 -m venv "$venv"
  "$venv/bin/python" -m pip install --quiet --upgrade pip
  "$venv/bin/python" -m pip install --quiet 'esptool==5.4.0'
fi
esptool="$venv/bin/esptool"

# Der Port gehoert root:uucp und ist fuer den Agenten nicht lesbar; der
# Zugriff laeuft daher ueber passwortloses sudo.
run_esptool() { sudo -n "$esptool" --chip esp32s3 --port "$port" --baud "$baud" "$@"; }

echo "=== 1. Chip und Flash-Groesse ==="
run_esptool flash-id 2>&1 | tee "$out_dir/flash-id.txt"
size_line="$(grep -oE 'Detected flash size: [0-9]+MB' "$out_dir/flash-id.txt" | head -1 || true)"
flash_mb="$(echo "$size_line" | grep -oE '[0-9]+' || true)"
flash_mb="${flash_mb:-16}"
echo "  Flash: ${flash_mb} MB, Baud: $baud"

echo
echo "=== 2. Vollstaendiges Abbild lesen (0x0 .. ${flash_mb}MB) ==="
full="$out_dir/full-flash.bin"
run_esptool read-flash 0x0 "$((flash_mb * 1024 * 1024))" "$full"
ls -la "$full" | awk '{print "  Groesse:", $5, "Bytes"}'

echo
echo "=== 3. NVS getrennt sichern (WLAN-Zugangsdaten) ==="
nvs="$out_dir/nvs.bin"
run_esptool read-flash 0x9000 0x7000 "$nvs"
ls -la "$nvs" | awk '{print "  Groesse:", $5, "Bytes"}'

echo
echo "=== 4. App-Deskriptor (Version, Build-Zeit, ELF-Hash) ==="
sudo -n python3 "$repo_dir/scripts/read-device-info.py" --port "$port" \
  2>&1 | tee "$out_dir/device-info.txt" || true

echo
echo "=== 5. Pruefsummen ==="
( cd "$out_dir" && sha256sum full-flash.bin nvs.bin > SHA256SUMS.txt )
sed 's/^/  /' "$out_dir/SHA256SUMS.txt"

cat > "$out_dir/README.txt" <<'TXT'
Sicherung des XiaoZhi-Geraets vor dem Firmware-Wechsel.

ENTHAELT ZUGANGSDATEN: full-flash.bin und nvs.bin enthalten den
NVS-Bereich mit den WLAN-Zugangsdaten des Geraets. Nicht veroeffentlichen,
nicht in ein Repository legen, nicht weitergeben.

Dateien
  full-flash.bin   vollstaendiges Flash-Abbild (Rueckweg per write-flash)
  nvs.bin          nur der NVS-Bereich (0x9000, 0x7000 Bytes)
  flash-id.txt     Chip und erkannte Flash-Groesse
  device-info.txt  App-Deskriptor: Version, Build-Zeit, ELF-SHA256
  SHA256SUMS.txt   Pruefsummen der Abbilder
TXT

echo
echo "Sicherung abgelegt in: $out_dir"
echo "ACHTUNG: enthaelt WLAN-Zugangsdaten — nicht veroeffentlichen."
