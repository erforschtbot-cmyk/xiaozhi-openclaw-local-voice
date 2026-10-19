#!/usr/bin/env python3
"""Compatibility entry point; exact baseline replaces the obsolete partial patcher."""
import subprocess
import sys
from pathlib import Path
root = Path(subprocess.check_output(["npm", "root", "-g"], text=True).strip()) / "openclaw"
print("Use restore-openclaw-baseline.py --apply explicitly; this entry point only checks.", flush=True)
subprocess.run([sys.executable, str(Path(__file__).with_name("restore-openclaw-baseline.py")),
                "--openclaw-root", str(root)], check=True)
raise SystemExit("Legacy apply entry point retired. No files changed; read docs/00-START-HIER.md.")
