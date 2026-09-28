#!/usr/bin/env bash
set -euo pipefail

# Erneuert CHECKSUMS.sha256 ueber alle veroeffentlichten Dateien.
#
# Die Dateiliste kommt aus git, nicht aus `find`. Damit gelten die Regeln der
# .gitignore automatisch — insbesondere bleiben die geklonten Upstream-Quellen
# (`source/`) und die Build-Artefakte (`build/`) aussen vor. Ein `find` ueber
# das Verzeichnis wuerde sie mit einsammeln (im Betrieb waren das 1786 fremde
# Dateien) und die Pruefsummendatei unbrauchbar machen.
#
# Erfasst werden getrackte UND noch nicht getrackte, aber nicht ignorierte
# Dateien — so erscheinen neue Dateien sofort, ohne vorher `git add`.

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_dir"

# Ausgeschlossen: die Pruefsummendatei selbst. Sonst steht ihr eigener
# Eintrag darin und die Pruefung schlaegt zwangslaeufig fehl, weil sich die
# Datei beim Schreiben aendert.
git ls-files --cached --others --exclude-standard -z \
  | grep -zv '^CHECKSUMS\.sha256$' \
  | LC_ALL=C sort -z \
  | xargs -0 sha256sum > CHECKSUMS.sha256

echo "CHECKSUMS.sha256 erneuert: $(wc -l < CHECKSUMS.sha256) Dateien"
