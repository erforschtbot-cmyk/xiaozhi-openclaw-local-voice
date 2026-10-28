#!/usr/bin/env python3
"""Liefert gerenderte Anzeigen als RGB565 oder PNG-Vorschau ueber HTTP aus.

Das Geraet holt sich das Bild selbst — es wird nichts geflasht und nichts im
Flash des ESP32 gespeichert. Der Server liest ausschliesslich aus
out/ und rendert bei Bedarf neu.

Start:
    python3 serve.py            # Port 8770, alle Adressen
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import re
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

BASE = Path(__file__).resolve().parent
OUT = BASE / "out"
RENDER = BASE / "render.py"

NAME_RE = re.compile(r"^[a-z0-9_-]+$")
_lock = threading.Lock()


def safe_name(value: str) -> str:
    if not NAME_RE.match(value or ""):
        raise ValueError(f"ungueltiger Name: {value!r}")
    return value


def render(template: str, data: dict) -> Path:
    """Rendert eine Vorlage neu und gibt den Pfad zur PNG zurueck."""
    template = safe_name(template)
    out = OUT / f"{template}.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, str(RENDER), template, "--out", str(out)]
    if data:
        cmd += ["--data", json.dumps(data, ensure_ascii=False)]
    with _lock:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        raise RuntimeError((result.stderr or result.stdout).strip() or "Render fehlgeschlagen")
    return out


class Handler(BaseHTTPRequestHandler):
    server_version = "jarvis-screen/1"

    def log_message(self, fmt, *args):  # kompakt und ohne Zeitstempel-Rauschen
        sys.stderr.write("screen: " + (fmt % args) + "\n")

    def _send_file(self, path: Path) -> None:
        if not path.is_file():
            self.send_error(404, "nicht gefunden")
            return
        body = path.read_bytes()
        ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        # Kein Caching: eine geaenderte Vorlage soll sofort greifen.
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802
        parsed = urlparse(self.path)
        path = unquote(parsed.path)
        query = parse_qs(parsed.query)

        if path in ("/", "/index.json"):
            files = sorted(p.name for p in OUT.glob("*.png")) if OUT.is_dir() else []
            body = json.dumps({"images": files}, ensure_ascii=False).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if path.startswith("/render/"):
            template = path[len("/render/"):].strip("/")
            data: dict = {}
            raw = (query.get("data") or [""])[0]
            if raw:
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError as exc:
                    self.send_error(400, f"data ist kein JSON: {exc}")
                    return
            try:
                self._send_file(render(template, data))
            except ValueError as exc:
                self.send_error(400, str(exc))
            except RuntimeError as exc:
                self.send_error(500, str(exc))
            return

        if path.startswith("/img/"):
            name = path[len("/img/"):].strip("/")
            if name.endswith(".png"):
                stem = name[:-4]
            elif name.endswith(".raw"):
                stem = name[:-4]
            else:
                stem = ""
            if not stem or not NAME_RE.match(stem) or not (OUT / name).is_file():
                self.send_error(400, "ungueltiger Name")
                return
            path = OUT / name
            if name.endswith(".raw"):
                body = path.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)
                return
            self._send_file(path)
            return

        self.send_error(404, "nicht gefunden")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8770)
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Anzeigen-Server auf http://{args.host}:{args.port}/", flush=True)
    print(f"  /render/<vorlage>?data={{...}}   neu rendern und ausliefern", flush=True)
    print(f"  /img/<vorlage>.png               zuletzt gerenderte Datei", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
