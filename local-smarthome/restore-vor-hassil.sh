#!/usr/bin/env bash
# Zurueck auf den Stand vor der HassIL-Integration.
#
# Aufruf:
#   ./restore-vor-hassil.sh            zeigt nur, was passieren wuerde
#   ./restore-vor-hassil.sh --apply    stellt wirklich zurueck
#
# Was zurueckgesetzt wird:
#   control.py     (HassIL als zweite Stufe wieder ausgebaut)
#   hassil_router.py, hassil-data/, NOTICE.md  (entfernt)
#
# Was NICHT angefasst wird:
#   backup-Ordner, names_overrides.json, catalog.json, rooms.json,
#   deine Geraetenamen und Raumzuordnungen.
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APPLY=0
[ "${1:-}" = "--apply" ] && APPLY=1

BK="$(ls -d "$DIR"/../alexa-direct-backup-vor-hassil-* 2>/dev/null | sort | tail -1)"
if [ -z "$BK" ] || [ ! -f "$BK/control.py" ]; then
    echo "FEHLER: kein Backup gefunden (alexa-direct-backup-vor-hassil-*)"
    exit 1
fi

echo "Backup: $BK"
echo

echo "Geplant:"
echo "  control.py  <- $BK/control.py"
for p in hassil_router.py NOTICE.md hassil-data; do
    [ -e "$DIR/$p" ] && echo "  entfernen   : $p"
done
echo

if [ "$APPLY" -eq 0 ]; then
    echo "Trockenlauf. Mit --apply wirklich ausfuehren."
    exit 0
fi

cp -a "$DIR/control.py" "$DIR/control.py.vor-restore-$(date +%Y%m%d-%H%M%S)"
cp -a "$BK/control.py" "$DIR/control.py"
rm -f "$DIR/hassil_router.py" "$DIR/NOTICE.md"
rm -rf "$DIR/hassil-data"

echo "Zurueckgesetzt."
echo "Jetzt neu starten:"
echo "  systemctl --user restart geraeteverwaltung.service"
echo "  systemctl --user restart jarvis-realtime-bridge.service"
