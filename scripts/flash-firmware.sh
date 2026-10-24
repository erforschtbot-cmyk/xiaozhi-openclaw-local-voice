#!/usr/bin/env bash
# Flasht die Jarvis-Firmware auf ein ESP32-S3-Board — mit Schutz gegen das
# falsche Image.
#
# Warum dieses Skript Board und Image selbst vergleicht:
# Auf einem Host liegen zwei verschiedene Firmware-Staende (Touch-LCD-4B und
# Audio-Board). Beide Boards sind ESP32-S3 mit 16 MB Flash, lassen sich am Chip
# also NICHT unterscheiden. Wer das falsche Image schreibt, hat ein bootendes
# aber stummes Geraet — oder ein Geraet, das gar nicht mehr startet.
#
# Deshalb gilt hier: das Image wird vor dem Schreiben EINDEUTIG einem Board
# zugeordnet. Passt es nicht zum erkannten bzw. angegebenen Board, bricht das
# Skript ab, bevor ein einziges Byte geschrieben wird.
set -euo pipefail

usage() {
  cat >&2 <<'EOF'
Usage: flash-firmware.sh --port /dev/ttyACM0 [--board auto|4b|audio] [--full] [--yes]

  --port PORT   Serieller Port (z.B. /dev/ttyACM0)
  --board       Zielboard. 'auto' (Standard) erkennt es am laufenden Geraet.
                4b    = Waveshare ESP32-S3-Touch-LCD-4B (Display, Gesicht)
                audio = Waveshare ESP32-S3-AUDIO-Board (Ring-LEDs, kein Display)
  --full        Vollimage inkl. Bootloader/Partitionstabelle/Assets.
                Nur bei leerem oder beschaedigtem Flash. Loescht NVS (WLAN).
                Fuer das Audio-Board existiert KEIN Vollimage im Repo.
  --yes         Ohne Rueckfrage schreiben (fuer automatisierte Laeufe)
  --from-build  build/firmware/xiaozhi.bin schreiben (frisch gebaute Firmware)
  --image PFAD  eigenes App-Image schreiben

Ohne --full wird nur die App-Partition (0x20000) geschrieben. WLAN/NVS und
Assets bleiben erhalten, das Geraet verbindet sich ohne Neu-Provisionierung.

Vor dem Schreiben wird das Image ueber seine eingebaute Board-Kennung gegen
--board geprueft. Passt es nicht, wird nichts geschrieben.

ACHTUNG: Ohne --from-build/--image wird das veroeffentlichte Prebuilt-Image
geschrieben, nicht ein lokaler Neubau. Nach ./scripts/build-firmware-*.sh
also --from-build mitgeben, sonst landet wieder der alte Stand auf dem Geraet.
EOF
  exit 2
}

port=""
board="auto"
full=0
assume_yes=0
custom_image=""
from_build=0
while (($#)); do
  case "$1" in
    --port) port="${2:-}"; shift 2 ;;
    --board) board="${2:-}"; shift 2 ;;
    --full|--full-image-i-understand) full=1; shift ;;
    --yes) assume_yes=1; shift ;;
    --from-build) from_build=1; shift ;;
    --image) custom_image="${2:-}"; shift 2 ;;
    -h|--help) usage ;;
    *) usage ;;
  esac
done
[[ -n "$port" ]] || usage
[[ -e "$port" ]] || { echo "Serial port missing: $port" >&2; exit 1; }
case "$board" in auto|4b|audio) ;; *) echo "Ungueltiges --board: $board" >&2; usage ;; esac

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_dir"

# --- Image-Auswahl ---------------------------------------------------------
# Jedes Image wird anhand seiner eingebetteten Board-Kennung klassifiziert.
# Das ist verlaesslicher als Dateinamen: der Dateiname sagt, wo etwas liegt,
# der String sagt, was drin ist.
#
# WICHTIG: `grep -a` direkt auf der Binaerdatei, NICHT `strings | grep -q`.
# Mit `set -o pipefail` liefert die Pipe 141 (SIGPIPE), sobald grep -q beim
# ersten Treffer schliesst — das Image gaelte dann faelschlich als 'unknown'.
classify_image() {
  local image="$1"
  if grep -aq "esp32-s3-audio-board" "$image"; then
    echo "audio"
  elif grep -aq "esp32-s3-touch-lcd-4b" "$image"; then
    echo "4b"
  else
    echo "unknown"
  fi
}

if ((full)); then
  image="$repo_dir/firmware/prebuilt/merged-binary.bin"
  offset="0x0"
  image_board="$(classify_image "$image")"
  if [[ "$image_board" == "audio" ]]; then
    : # Platzhalter: es gibt derzeit kein Audio-Vollimage im Repo
  fi
  if [[ "$image_board" != "4b" ]]; then
    echo "FEHLER: Das Vollimage gehoert nicht zum 4B (erkannt: $image_board)." >&2
    exit 1
  fi
  echo "Vollimage: $image (Board: 4B, Offset 0x0)"
  echo "WARNUNG: Ein Vollimage loescht NVS und Assets. WLAN muss neu eingerichtet werden."
  if [[ "$board" == "audio" ]]; then
    echo "FEHLER: Fuer das Audio-Board gibt es KEIN Vollimage im Repo." >&2
    echo "        Ein leeres Audio-Board braucht einen frischen Build:" >&2
    echo "          ./scripts/build-firmware-audio-board.sh <HOST_IM_GERAETENETZ>" >&2
    echo "        Danach build/firmware/merged-binary.bin mit --board audio --full schreiben." >&2
    exit 1
  fi
else
  case "$board" in
    4b)
      image="$repo_dir/firmware/prebuilt/xiaozhi.bin"
      ;;
    audio)
      image="$repo_dir/firmware/prebuilt/audio-board/xiaozhi.bin"
      ;;
    auto)
      echo "FEHLER: --board auto ist ohne Geraete-Erkennung nicht eindeutig." >&2
      echo "        Beide Boards sind ESP32-S3 mit 16 MB Flash; der Chip verraet das" >&2
      echo "        Modell nicht. Board aus dem Boot-Log ablesen:" >&2
      echo "          SKU=esp32-s3-audio-board      -> --board audio" >&2
      echo "          SKU=esp32-s3-touch-lcd-4b     -> --board 4b" >&2
      echo "        Alternativ im Build/Manifest nachsehen (board_dir)." >&2
      exit 1
      ;;
  esac
  # Eigenes Image bzw. frischer Build hat Vorrang vor dem Prebuilt. Ohne diese
  # beiden Schalter wuerde ein gerade gebautes Image nie auf das Geraet kommen.
  if ((from_build)) && [[ -n "$custom_image" ]]; then
    echo "FEHLER: --from-build und --image schliessen sich aus." >&2
    exit 1
  fi
  if ((from_build)); then
    image="$repo_dir/build/firmware/xiaozhi.bin"
    [[ -f "$image" ]] || {
      echo "FEHLER: Kein Build gefunden: $image" >&2
      echo "        Erst bauen: ./scripts/build-firmware-audio-board.sh <HOST>" >&2
      exit 1
    }
  elif [[ -n "$custom_image" ]]; then
    image="$(cd "$(dirname "$custom_image")" && pwd)/$(basename "$custom_image")"
    [[ -f "$image" ]] || { echo "FEHLER: Image fehlt: $image" >&2; exit 1; }
  fi
  offset="0x20000"
  image_board="$(classify_image "$image")"
  if [[ "$image_board" != "$board" ]]; then
    echo "FEHLER: Bild passt nicht zum Zielboard." >&2
    echo "        Gewaehlt: --board $board" >&2
    echo "        Image  : $image" >&2
    echo "        Enthaelt: $image_board" >&2
    echo "        Es wird NICHTS geschrieben." >&2
    exit 1
  fi
  echo "App-Image: $image (Board: $board, Offset $offset)"
fi

# --- Pruefsumme ------------------------------------------------------------
actual="$(sha256sum "$image" | awk '{print $1}')"
if ((from_build)) || [[ -n "$custom_image" ]]; then
  # Ein lokaler Neubau steht nicht in CHECKSUMS.sha256. Stattdessen gegen das
  # Manifest pruefen, das der Build selbst geschrieben hat.
  manifest="$(dirname "$image")/manifest.json"
  manifest_sha=""
  if [[ -f "$manifest" ]]; then
    manifest_sha="$(python3 - "$manifest" "$(basename "$image")" <<'PY'
import json, sys
try:
    data = json.load(open(sys.argv[1]))
    for artifact in data.get('artifacts', []):
        if artifact.get('file') == sys.argv[2]:
            print(artifact.get('sha256', ''))
            break
except Exception:
    pass
PY
)"
  fi
  if [[ -n "$manifest_sha" && "$manifest_sha" != "$actual" ]]; then
    echo "FEHLER: Image weicht vom Build-Manifest ab (beschaedigt?)." >&2
    echo "        manifest: $manifest_sha" >&2
    echo "        datei   : $actual" >&2
    exit 1
  fi
  if [[ -n "$manifest_sha" ]]; then
    echo "Pruefsumme OK (gegen Build-Manifest): $actual"
  else
    echo "Pruefsumme: $actual (kein Manifest-Eintrag, lokaler Build)"
  fi
else
  expected="$(grep -E "  ${image#"$repo_dir"/}$" "$repo_dir/CHECKSUMS.sha256" | awk '{print $1}')"
  if [[ -z "$expected" ]]; then
    echo "FEHLER: Kein Eintrag fuer ${image#"$repo_dir"/} in CHECKSUMS.sha256." >&2
    exit 1
  fi
  if [[ "$actual" != "$expected" ]]; then
    echo "FEHLER: Pruefsumme weicht ab. Datei beschaedigt oder veraendert." >&2
    echo "        erwartet: $expected" >&2
    echo "        gefunden: $actual" >&2
    exit 1
  fi
  echo "Pruefsumme OK: $actual"
fi

# --- App-Partitionsgroesse pruefen -----------------------------------------
if [[ "$offset" == "0x20000" ]]; then
  app_limit=$((4032 * 1024))
  image_size=$(stat -c %s "$image")
  if ((image_size > app_limit)); then
    echo "FEHLER: Image ($image_size B) ist groesser als die App-Partition ($app_limit B)." >&2
    exit 1
  fi
  echo "Groesse OK: $image_size B < $app_limit B"
fi

# --- Rueckfrage ------------------------------------------------------------
if ((!assume_yes)); then
  printf '%s\n' "" \
    "Gerät:   $port" \
    "Board:   $board" \
    "Image:   ${image#"$repo_dir"/}" \
    "Offset:  $offset" \
    "" \
    "Jetzt schreiben? [j/N]"
  read -r answer || answer=""
  case "$answer" in j|J|y|Y|ja) ;; *) echo "Abgebrochen. Nichts geschrieben."; exit 0 ;; esac
fi

# --- esptool im eigenen venv ----------------------------------------------
# Nicht auf ein globales esptool hoffen: die Version muss zur Firmware passen.
venv_dir="$repo_dir/.venv-esptool"
if [[ ! -x "$venv_dir/bin/esptool" ]]; then
  echo "Installiere esptool==5.4.0 in $venv_dir ..."
  python3 -m venv "$venv_dir"
  "$venv_dir/bin/python" -m pip install --quiet --upgrade pip
  "$venv_dir/bin/python" -m pip install --quiet 'esptool==5.4.0'
fi
esptool="$venv_dir/bin/esptool"

# --- Schreiben und pruefen -------------------------------------------------
echo "Schreibe ..."
"$esptool" --chip esp32s3 --port "$port" -b 460800 \
  write-flash --flash-mode dio --flash-size 16MB --flash-freq 80m \
  "$offset" "$image"

echo "Pruefe geschriebene Daten ..."
"$esptool" --chip esp32s3 --port "$port" verify-flash "$offset" "$image"

echo
echo "Flash und Pruefung abgeschlossen."
echo "Wenn das Geraet nicht startet: USB abziehen/einstecken, dann"
echo "  journalctl --user -u jarvis-realtime-bridge -n 50 --no-pager"
