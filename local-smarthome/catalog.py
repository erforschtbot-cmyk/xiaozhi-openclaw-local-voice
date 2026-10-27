"""Geraetekatalog aus dem Alexa2-Adapter (ioBroker) aufbauen.

Quelle sind die echten Objekte unter alexa2.0.Smart-Home-Devices.*, keine
geratenen Namen. Wichtig: getObjects liefert die Kanal-Objekte (also die
Geraetenamen) auf diesem ioBroker NICHT mit, deshalb wird der Kanal jedes
Geraets einzeln ueber /get/<id> gelesen.
"""
from __future__ import annotations

import concurrent.futures as cf
import json
import os
import time
import urllib.request

BASE = os.environ.get("IOBROKER_SIMPLE_API", "http://192.168.178.24:8087")
ADAPTER = os.environ.get("ALEXA2_INSTANCE", "alexa2.0")
PREFIX = f"{ADAPTER}.Smart-Home-Devices."
HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "catalog.json")
MAX_AGE = 6 * 3600


def _json_url(url, timeout=25):
    with urllib.request.urlopen(url, timeout=timeout) as response:
        return json.loads(response.read().decode())


def build():
    objects = _json_url(BASE + "/getObjects", timeout=40)
    states = {}
    device_ids = set()
    for oid, obj in objects.items():
        if not oid.startswith(PREFIX):
            continue
        rest = oid[len(PREFIX):]
        if "." not in rest:
            continue
        dev_id, state = rest.split(".", 1)
        device_ids.add(dev_id)
        common = obj.get("common", {}) or {}
        states.setdefault(dev_id, {})[state] = {
            "write": bool(common.get("write")),
            "type": common.get("type"),
            "role": common.get("role"),
        }

    def read_channel(dev_id):
        try:
            obj = _json_url(f"{BASE}/get/{PREFIX}{dev_id}/", timeout=10)
        except Exception:
            return dev_id, None
        common = obj.get("common", {}) or {}
        native = obj.get("native", {}) or {}
        return dev_id, {
            "name": (common.get("name") or "").strip(),
            "manufacturer": native.get("manufacturerName"),
            "model": native.get("modelName"),
            "description": native.get("friendlyDescription"),
        }

    with cf.ThreadPoolExecutor(max_workers=12) as pool:
        channels = dict(pool.map(read_channel, sorted(device_ids)))

    devices = []
    for dev_id, meta in channels.items():
        if not meta or not meta["name"]:
            continue
        writable = sorted(
            state for state, info in states.get(dev_id, {}).items()
            if info["write"] and not state.startswith("#")
        )
        if not writable:
            continue
        devices.append({
            "id": f"{PREFIX}{dev_id}",
            "name": meta["name"],
            "manufacturer": meta["manufacturer"],
            "model": meta["model"],
            "description": meta["description"],
            "writable": writable,
        })
    devices.sort(key=lambda item: item["name"].lower())
    payload = {"builtAt": int(time.time()), "base": BASE, "devices": devices}
    tmp = CACHE + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=1)
        os.replace(tmp, CACHE)
    except OSError:
        # Der Jarvis-Dienst laeuft mit ProtectHome=read-only und darf hier nicht
        # schreiben. Der Katalog bleibt dann im Speicher gueltig; geschrieben
        # wird er beim naechsten Lauf ausserhalb der Sandbox.
        pass
    return payload


def load(refresh=False):
    if not refresh and os.path.exists(CACHE):
        try:
            with open(CACHE, encoding="utf-8") as handle:
                payload = json.load(handle)
            if payload.get("devices"):
                # Auch ein aelterer Cache wird benutzt: im Jarvis-Dienst ist
                # $HOME read-only, ein Neuaufbau kostet dort rund 1-3 s pro
                # Befehl. Erneuert wird ausserhalb der Sandbox mit
                # "python3 <modul>.py"; das ist der einzige vorgesehene Weg.
                return payload
        except (OSError, ValueError):
            pass
    return build()


if __name__ == "__main__":
    data = build()
    print(f"catalog.json: {len(data['devices'])} schaltbare Geraete")
    for dev in data["devices"][:5]:
        print(" ", dev["name"], "|", dev["writable"])
