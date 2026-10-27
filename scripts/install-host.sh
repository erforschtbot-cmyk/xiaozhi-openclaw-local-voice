#!/usr/bin/env bash
# Baut die Jarvis-Bridge auf einem frischen Host auf.
#
# Zielgruppe: auch ein Agent ohne Vorwissen. Deshalb:
#   - kopiert ALLE Module, die eine Importkette braucht (nicht nur den Einstieg)
#   - prueft am Ende jede erwartete Datei einzeln
#   - prueft die Python-Importkette wirklich, statt sie anzunehmen
#   - bricht mit klarer Meldung ab, wenn etwas fehlt
set -euo pipefail

usage() {
  echo "Usage: $0 --public-host LAN_IP [OPTIONEN]" >&2
  echo "       --install-dir PATH   Zielverzeichnis (Standard ~/.local/share/jarvis-realtime-bridge)" >&2
  echo "       --stt-uri HOST:PORT  Adresse des lokalen STT-Dienstes (Standard 127.0.0.1:10303)" >&2
  echo "       --tts-voice NAME     Piper-Stimme (Standard de_DE-kerstin-low)" >&2
  echo "       --tts-voice-dir PATH Verzeichnis der Piper-Stimmen (Standard ~/.local/share/piper-voices)" >&2
  echo "       --tts-split MODUS    clause | sentence | whole (Standard: sentence)" >&2
  echo "       --provider-stt       ohne lokale Erkennung (OpenAI hoert zu)" >&2
  echo "       --provider-voice     ohne lokale Sprachausgabe (OpenAI-Stimme)" >&2
  echo "       --no-restart         Unit schreiben, aber nicht starten" >&2
  exit 2
}

public_host=""
tts_voice="de_DE-kerstin-low"
tts_voice_dir="$HOME/.local/share/piper-voices"
tts_split="sentence"
stt_uri="127.0.0.1:10303"
local_voice=1
local_stt=1
restart=1
install_dir="${XDG_DATA_HOME:-$HOME/.local/share}/jarvis-realtime-bridge"
while (($#)); do
  case "$1" in
    --public-host) public_host="${2:-}"; shift 2 ;;
    --install-dir) install_dir="${2:-}"; shift 2 ;;
    --tts-voice) tts_voice="${2:-}"; shift 2 ;;
    --tts-voice-dir) tts_voice_dir="${2:-}"; shift 2 ;;
    --tts-split) tts_split="${2:-}"; shift 2 ;;
    --stt-uri) stt_uri="${2:-}"; shift 2 ;;
    --provider-stt) local_stt=0; shift ;;
    --provider-voice) local_voice=0; shift ;;
    --no-restart) restart=0; shift ;;
    *) usage ;;
  esac
done

case "$tts_split" in
  clause|sentence|whole) ;;
  *) echo "Ungueltiger --tts-split Wert: $tts_split" >&2; usage ;;
esac

# Lokale Erkennung setzt lokale Sprachausgabe voraus: der lokale Helfer
# liefert gar kein Audio; ohne Piper gaebe es nichts zu hoeren.
if (( local_stt )); then
  local_voice=1
fi
[[ -n "$public_host" ]] || usage

for command_name in python3 node systemctl; do
  command -v "$command_name" >/dev/null || { echo "Missing command: $command_name" >&2; exit 1; }
done

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
mkdir -p "$install_dir" "$HOME/.config/systemd/user"
local_smarthome_dir="${XDG_DATA_HOME:-$HOME/.local/share}/alexa-direct"
mkdir -p "$local_smarthome_dir"

# --- Laufzeitcode: ALLE Module, nicht nur das Einstiegsprogramm ------------
# Warum vollstaendig: server.py importiert room_command hart. Der Gateway-Patch
# importiert voice-turn-context.mjs und scoped-smart-home-router.mjs statisch.
# Fehlt eine dieser Dateien, startet die Bridge nicht — oder der Gateway-Handler
# laedt nicht mehr. Nur den Einstieg zu kopieren ist die klassische Sackgasse.
#
# Format: Quelldatei:Zielmodus. 0644 fuer Daten, 0755 fuer Programme.
runtime_files=(
  "server.py:0644"
  "room_command.py:0644"
  "piper_speaker.py:0644"
  "local_stt.mjs:0644"
  "local-voice.mjs:0755"
  "openclaw-talk-realtime.mjs:0755"
  "openclaw-thin-client.mjs:0755"
  "photo-consult.mjs:0755"
  "voice-turn-context.mjs:0644"
  "room-command.mjs:0644"
  "room-router.mjs:0644"
  "smart-home-router.mjs:0644"
  "scoped-smart-home-router.mjs:0644"
  "refresh-alexa-catalog.mjs:0644"
  "requirements.txt:0644"
  "requirements-tts.txt:0644"
)

for entry in "${runtime_files[@]}"; do
  src="${entry%:*}"
  mode="${entry##*:}"
  if [[ ! -f "$repo_dir/gateway/$src" ]]; then
    echo "FEHLT im Repo: gateway/$src — Abbruch, sonst startet die Bridge spaeter nicht." >&2
    exit 1
  fi
  install -m "$mode" "$repo_dir/gateway/$src" "$install_dir/$src"
done

# Lokaler Smart-Home-Schnellweg. Python-Quellen werden aktualisiert; die aus
# ioBroker erzeugten Kataloge bleiben unversioniert. Gepflegte Zuordnungen
# werden beim ersten Lauf angelegt und bei spaeteren Installationen erhalten.
for source_file in catalog.py control.py fuzzy.py localmap.py lookup.py resolver.py rooms.py smarthome_cli.py; do
  install -m 0644 "$repo_dir/local-smarthome/$source_file" "$local_smarthome_dir/$source_file"
done
install -m 0644 "$repo_dir/local-smarthome/test_resolver.py" "$local_smarthome_dir/test_resolver.py"
for config_file in bridges.json rooms_overrides.json; do
  if [[ ! -e "$local_smarthome_dir/$config_file" ]]; then
    install -m 0644 "$repo_dir/local-smarthome/$config_file" "$local_smarthome_dir/$config_file"
  fi
done

# --- Konfiguration, die vorhanden sein MUSS -------------------------------
# room-map.json ist keine Laufzeitdatei: ohne sie bekommt kein Geraet einen
# Raum und "Licht an" landet in keinem Raum. Bestehende Zuordnungen bleiben
# bei erneutem Installieren erhalten.
if [[ ! -e "$install_dir/room-map.json" ]]; then
  install -m 0644 "$repo_dir/gateway/room-map.json" "$install_dir/room-map.json"
  echo "room-map.json aus Vorlage angelegt — Device-Ids noch eintragen (siehe Datei)."
fi

# camera-orientation.json: Kameralage pro Geraet. Ohne sie bleibt die
# Sensoreinstellung unveraendert — die Bridge laeuft trotzdem. Bestehende
# Eintraege bleiben bei erneutem Installieren erhalten.
if [[ ! -e "$install_dir/camera-orientation.json" ]]; then
  install -m 0644 "$repo_dir/gateway/camera-orientation.json" "$install_dir/camera-orientation.json"
  echo "camera-orientation.json aus Vorlage angelegt — Device-Ids/Lage eintragen (siehe Datei)."
fi

# Modus-Schalter: Existenz der Datei schaltet den alten Bridge-Direktpfad AUS.
# Genau EIN Ausfuehrer (die Gateway-Weiche) soll schalten. Leere Datei = korrekt.
install -m 0644 "$repo_dir/gateway/disable-smart-home-fastpath" "$install_dir/disable-smart-home-fastpath"

# Offline-Tests mitinstallieren: die Pruefskripte erwarten sie am Zielort.
for test_file in "$repo_dir"/gateway/test-*.mjs "$repo_dir"/gateway/test_*.py; do
  [[ -f "$test_file" ]] || continue
  install -m 0644 "$test_file" "$install_dir/"
done

# Katalog-Units mitliefern, aber NICHT aktivieren: der Timer wuerde einen
# fremden ioBroker kontaktieren, bevor der Betreiber seinen Endpunkt eintraegt.
install -m 0644 "$repo_dir/systemd/jarvis-alexa-catalog.service" "$HOME/.config/systemd/user/"
install -m 0644 "$repo_dir/systemd/jarvis-alexa-catalog.timer" "$HOME/.config/systemd/user/"

# --- Python-Umgebung ------------------------------------------------------
python3 -m venv "$install_dir/.venv"
"$install_dir/.venv/bin/python" -m pip install --upgrade pip >/dev/null
"$install_dir/.venv/bin/python" -m pip install -r "$install_dir/requirements.txt"

if (( local_voice )); then
  # Piper in dasselbe Venv installieren, in dem die Bridge laeuft. So findet
  # der Bridge-Prozess das Modul ohne zusaetzliche PYTHONPATH-Tricks.
  "$install_dir/.venv/bin/python" -m pip install -r "$repo_dir/gateway/requirements-tts.txt"
  if [[ ! -f "$tts_voice_dir/$tts_voice.onnx" ]]; then
    echo "Hinweis: Stimme $tts_voice nicht unter $tts_voice_dir gefunden." >&2
    echo "        Stimmen holen:  python3 -m piper.download_voices $tts_voice" >&2
    echo "        Ziel-Verzeichnis: $tts_voice_dir" >&2
    echo "        oder Paket piper-voices-common der Distribution verwenden." >&2
  fi
fi

# --- systemd-Units --------------------------------------------------------
render_unit() {
  local target="$1" local_stt_flag="$2" local_voice_flag="$3"
  python3 - "$repo_dir/systemd/jarvis-realtime-bridge.service.in" "$target" \
    "$install_dir" "$public_host" "$tts_voice" "$tts_voice_dir" "$stt_uri" \
    "$tts_split" "$local_stt_flag" "$local_voice_flag" \
    "$npm_root" "$session_key" "$consult_session_key" <<'PY'
from pathlib import Path
import sys
(
    source, target, install_dir, public_host, tts_voice,
    tts_voice_dir, stt_uri, tts_split, local_stt, local_voice,
    npm_root, session_key, consult_session_key,
) = sys.argv[1:]
text = (
    Path(source).read_text()
    .replace("@INSTALL_DIR@", install_dir)
    .replace("@PUBLIC_HOST@", public_host)
    .replace("@TTS_VOICE@", tts_voice)
    .replace("@TTS_VOICE_DIR@", tts_voice_dir)
    .replace("@STT_URI@", stt_uri)
    .replace("@TTS_SPLIT@", tts_split)
    .replace("@NPM_ROOT@", npm_root)
    .replace("@SESSION_KEY@", session_key)
    .replace("@CONSULT_SESSION_KEY@", consult_session_key)
)
if local_stt == "0":
    text = text.replace(f" --stt-local --stt-uri {stt_uri} --stt-language de", "")
if local_voice == "0":
    text = text.replace(
        f" --tts-local --tts-voice {tts_voice} --tts-voice-dir {tts_voice_dir}"
        f" --tts-split {tts_split}",
        "",
    )
Path(target).write_text(text)
PY
}

# Werte, die die Unit braucht. npm_root wird ERMITTELT, nicht geraten: der
# Helfer laedt dist/plugin-sdk/gateway-runtime.js aus der globalen Installation.
# OPENCLAW_NPM_ROOT hat Vorrang (nuetzlich, wenn npm-Prefix nicht Standard ist).
npm_root="${OPENCLAW_NPM_ROOT:-$(npm root -g 2>/dev/null || true)}"
if [[ -z "$npm_root" || ! -d "$npm_root/openclaw/dist" ]]; then
  echo "FEHLER: Kein OpenClaw-Paket unter '$npm_root/openclaw/dist'." >&2
  echo "        OpenClaw global installieren: npm install -g openclaw" >&2
  echo "        Oder Pfad vorgeben: OPENCLAW_NPM_ROOT=/pfad/zu/node_modules $0 ..." >&2
  exit 1
fi
session_key="agent:voice:xiaozhi-realtime-v6"
consult_session_key="agent:voice:xiaozhi-consult-v6"

unit_target="$HOME/.config/systemd/user/jarvis-realtime-bridge.service"
render_unit "$unit_target" "$local_stt" "$local_voice"

# Umschalter lokal <-> realtime: beide Profile mitliefern, damit ein Wechsel
# spaeter ohne Neuinstallation moeglich ist.
render_unit "$install_dir/profile-local.service" "1" "1"
render_unit "$install_dir/profile-realtime.service" "0" "0"
cat > "$install_dir/voice-mode" <<'EOF'
#!/usr/bin/env bash
# Schaltet die Bridge zwischen lokaler und Provider-Sprachkette um.
set -euo pipefail
case "${1:-}" in local|realtime) ;; *) echo 'Usage: voice-mode local|realtime'; exit 2;; esac
base="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cp "$base/profile-$1.service" "$HOME/.config/systemd/user/jarvis-realtime-bridge.service"
systemctl --user daemon-reload
systemctl --user restart jarvis-realtime-bridge.service
systemctl --user is-active jarvis-realtime-bridge.service
EOF
chmod 0755 "$install_dir/voice-mode"

# --- Selbstpruefung: Dateien -------------------------------------------------
missing=0
for required in "${runtime_files[@]%:*}"; do
  if [[ ! -e "$install_dir/$required" ]]; then
    echo "MISSING: $install_dir/$required" >&2
    missing=1
  fi
done
for required in room-map.json disable-smart-home-fastpath voice-mode camera-orientation.json; do
  if [[ ! -e "$install_dir/$required" ]]; then
    echo "MISSING: $install_dir/$required" >&2
    missing=1
  fi
done
for required in catalog.py control.py fuzzy.py localmap.py resolver.py rooms.py smarthome_cli.py bridges.json rooms_overrides.json; do
  if [[ ! -e "$local_smarthome_dir/$required" ]]; then
    echo "MISSING: $local_smarthome_dir/$required" >&2
    missing=1
  fi
done
if (( missing )); then
  echo "Installation unvollstaendig — Abbruch vor dem Start." >&2
  exit 1
fi

# --- Selbstpruefung: Importkette --------------------------------------------
# Dateien vorhanden ist nicht genug. Der Klassiker: server.py liegt da, aber
# ein importiertes Modul fehlt und der Dienst stirbt beim ersten Start.
if ! ( cd "$install_dir" && "$install_dir/.venv/bin/python" - <<'PY'
import importlib.util, sys
spec = importlib.util.spec_from_file_location("jarvis_server", "server.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
print("import OK: server.py inkl. room_command, opuslib, websockets")
PY
); then
  echo "Importkette FEHLGESCHLAGEN. Die Bridge wuerde beim Start abbrechen." >&2
  echo "Haeufigste Ursache: eine Datei fehlt in gateway/ oder pip ist unvollstaendig." >&2
  exit 1
fi

# --- Start ----------------------------------------------------------------
if (( restart )); then
  systemctl --user daemon-reload
  systemctl --user enable --now jarvis-realtime-bridge.service
  if ! systemctl --user is-active --quiet jarvis-realtime-bridge.service; then
    echo "Dienst startet nicht. Log: journalctl --user -u jarvis-realtime-bridge -n 50" >&2
    exit 1
  fi
fi

echo
echo "Installed: jarvis-realtime-bridge.service  ($install_dir)"
if (( local_stt )); then
  echo "  recognition: lokal  -> $stt_uri  (Dienst muss laufen, siehe docs/10-CANARY-FAVORIT.md)"
else
  echo "  recognition: provider (OpenAI)"
fi
if (( local_voice )); then
  echo "  voice:       lokal  -> Piper $tts_voice, split=$tts_split"
else
  echo "  voice:       provider (OpenAI-Stimme)"
fi
cat <<'EOF'

Noch zu tun (bewusst nicht automatisiert):
  1. room-map.json: eigene Device-Ids eintragen, sonst kein Raumbezug.
  2. STT-Dienst muss auf der angegebenen Adresse laufen.
  3. Gateway-TLS/Token muss passen: ~/.openclaw/ssl/gateway.crt und
     gateway.authToken in ~/.openclaw/secrets.json.
  4. Pruefen: python3 scripts/check-new-host.py
  5. Erst danach Firmware flashen und den Testplan (docs/04-TESTPLAN.md) fahren.
EOF
