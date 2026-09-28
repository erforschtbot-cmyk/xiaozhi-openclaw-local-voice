#!/usr/bin/env bash
set -euo pipefail

# Erneuert CHECKSUMS.sha256 ueber alle veroeffentlichten Dateien.
#
# Nicht enthalten sind die Pruefsummendatei selbst und Verzeichnisse, die
# ohnehin nicht ins Repository gehoeren (siehe .gitignore).

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$repo_dir"

find . -type f \
  -not -path './.git/*' \
  -not -path './.venv/*' \
  -not -path '*/__pycache__/*' \
  -not -name 'CHECKSUMS.sha256' \
  -not -name '*.pyc' \
  | LC_ALL=C sort \
  | xargs sha256sum > CHECKSUMS.sha256

echo "CHECKSUMS.sha256 erneuert: $(wc -l < CHECKSUMS.sha256) Dateien"
