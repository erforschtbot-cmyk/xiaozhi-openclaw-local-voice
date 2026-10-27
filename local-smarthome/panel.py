#!/usr/bin/env python3
"""Geraeteverwaltung im Browser - die Oberflaeche, die Alexa nicht hergibt.

Was hier geht:
  * alle Geraete sehen, mit aktuellem Zustand
  * Raeume sehen und Geraete einem Raum zuordnen
  * Sprechnamen veraendern (wie in der Alexa-App)
  * einen Satz testen: was wuerde passieren, welcher Datenpunkt

Wichtig: Das hier ist eine Verwaltung, keine zweite Wahrheit. Quelle bleibt
ioBroker; die Namen liegen als Overrides daneben (names_overrides.json) und
werden beim Laden ueber die Geraeteliste gelegt.

Bewusst ohne Fremdpaket: http.server aus der Standardbibliothek. Kein Flask,
kein FastAPI, nichts zu installieren - der Host hat keine Web-Bibliothek.
"""
from __future__ import annotations

import json
import os
import sys
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import catalog
import control
import localmap
import overrides
import resolver

BASE = os.environ.get("IOBROKER_SIMPLE_API", "http://192.168.178.24:8087")
CAPS_ORDER = ("onoff", "brightness", "color", "colortemp", "position",
              "temperature", "targetTemperature")


def _http_json(url: str, payload=None, timeout: float = 8.0):
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode()
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode() or "null")


def live_values(datapoints: list) -> dict:
    """Aktuelle Werte in EINEM Aufruf. Fehler duerfen die Seite nicht kippen."""
    wanted = [dp for dp in dict.fromkeys(datapoints) if dp]
    out = {}
    for start in range(0, len(wanted), 80):
        chunk = wanted[start:start + 80]
        url = f"{BASE}/getBulk/" + ",".join(urllib.parse.quote(dp, safe="") for dp in chunk)
        try:
            rows = _http_json(url) or []
        except Exception:
            continue
        for row in rows:
            if isinstance(row, dict) and row.get("id"):
                out[row["id"]] = {"val": row.get("val"), "ts": row.get("ts"), "ack": row.get("ack")}
    return out


def datapoint_rooms() -> dict:
    """Datenpunkt -> Raum, aus rooms.json und den Overrides.

    rooms.json ist die abgeleitete Sicht (Skripte + Aliasse + bestaetigte
    Zuordnungen). Sie nennt je Raum die Datenpunkte; daraus laesst sich jedes
    Geraet seinem Raum zuordnen, ohne dass es doppelt gepflegt werden muss.
    """
    mapping = {}
    try:
        with open(os.path.join(HERE, "rooms.json"), encoding="utf-8") as handle:
            data = json.load(handle) or {}
    except (OSError, ValueError):
        data = {}
    for entry in data.get("rooms") or []:
        room = (entry.get("room") or "").strip()
        if not room:
            continue
        for dp in entry.get("lights") or []:
            mapping[dp] = room
    # Vom Eigentuemer im Panel gesetzte Zuordnungen haben Vorrang.
    try:
        for ident, room in (overrides.load().get("rooms") or {}).items():
            mapping[ident] = room
    except Exception:
        pass
    return mapping


def collect():
    """Geraete aus beiden Quellen zusammenfuehren.

    localmap hat belegte lokale Ziele, catalog den Alexa-Weg. Ein Geraet kann in
    beiden stehen; dann gilt das lokale Ziel, weil es ohne Amazon schaltet.
    """
    local = {}
    try:
        for dev in localmap.load(include_deleted=True).get("devices") or []:
            caps = dev.get("caps") or {}
            target = None
            for cap in CAPS_ORDER:
                if cap in caps:
                    target = caps[cap]
                    break
            local[(dev.get("name") or "").casefold()] = {
                "deleted": bool(dev.get("deleted")),
                "name": dev.get("name"),
                "baseName": dev.get("baseName") or dev.get("name"),
                "caps": sorted(caps),
                "target": target,
                "route": "local",
                "broken": dev.get("broken") or [],
                "room": dev.get("room"),
            }
    except Exception as exc:  # noqa: BLE001
        print(f"localmap nicht lesbar: {exc!r}", flush=True)

    devices = []
    for dev in catalog.load(include_deleted=True).get("devices") or []:
        name = dev.get("name") or ""
        key = name.casefold()
        writable = [c for c in (dev.get("writable") or []) if c]
        entry = {
            "id": dev.get("id"),
            "deleted": bool(dev.get("deleted")),
            "name": name,
            "baseName": dev.get("baseName") or name,
            "caps": writable,
            "target": f"{dev.get('id')}.{writable[0]}" if dev.get("id") and writable else None,
            "route": "alexa2-cloud",
            "manufacturer": dev.get("manufacturer") or "",
            "broken": [],
            "room": dev.get("room"),
        }
        hit = local.pop(key, None)
        if hit:
            entry["local"] = {"caps": hit["caps"], "target": hit["target"],
                              "broken": hit["broken"]}
            entry["route"] = "local"
            if hit["target"]:
                entry["target"] = hit["target"]
            entry["caps"] = sorted(set(entry["caps"]) | set(hit["caps"]))
        devices.append(entry)
    # Reine Lokalgeraete, die im Alexa-Katalog fehlen.
    for hit in local.values():
        devices.append({
            "id": None,
            "deleted": bool(hit.get("deleted")),
            "name": hit["name"],
            "baseName": hit["baseName"],
            "caps": hit["caps"],
            "target": hit["target"],
            "route": "local",
            "manufacturer": "",
            "broken": hit["broken"],
            "room": hit["room"],
        })

    by_dp = datapoint_rooms()
    try:
        vorgegeben = overrides.load().get("targets") or {}
    except Exception:
        vorgegeben = {}
    for dev in devices:
        if not dev.get("room"):
            dev["room"] = by_dp.get(dev.get("target")) or by_dp.get((dev.get("local") or {}).get("target"))
        # Vom Eigentuemer vorgegebenes Ziel: anzeigen und den Loeschknopf
        # einblenden. Die Kennung ist die id, sonst der Name.
        ident = dev.get("id") or dev.get("name")
        # Automatisch ermitteltes Ziel merken, bevor eine Vorgabe es
        # ueberschreibt. Der X-Knopf in der Oberflaeche setzt das Feld darauf
        # zurueck; ohne diesen Wert wuerde er es leeren.
        dev["autoTarget"] = dev.get("target")
        vorgabe = vorgegeben.get(ident)
        if isinstance(vorgabe, dict) and vorgabe.get("datapoint"):
            dev["target"] = vorgabe["datapoint"]
            dev["targetOverridden"] = True
        elif isinstance(vorgabe, str) and vorgabe:
            dev["target"] = vorgabe
            dev["targetOverridden"] = True
        else:
            dev["targetOverridden"] = False

    values = live_values([d["target"] for d in devices if d.get("target")])
    for dev in devices:
        target = dev.get("target")
        info = values.get(target) if target else None
        dev["state"] = None if info is None else info.get("val")
        dev["stateAt"] = None if info is None else info.get("ts")
        # Manche Kanaele sind reine Befehle ohne Rueckkanal (read: false).
        # Dann gibt es keinen Zustand zu zeigen - das ist kein Fehler.
        dev["readable"] = bool(info is not None and info.get("val") is not None
                               or (info or {}).get("ts"))
    # Ausgeblendete ans Ende, damit sie die normale Liste nicht stoeren.
    devices.sort(key=lambda d: (bool(d.get("deleted")),
                                (d.get("room") or "").casefold(),
                                (d.get("name") or "").casefold()))
    return devices


def room_list(devices):
    seen = {}
    try:
        with open(os.path.join(HERE, "rooms.json"), encoding="utf-8") as handle:
            for entry in (json.load(handle) or {}).get("rooms") or []:
                room = (entry.get("room") or "").strip()
                if room:
                    seen[room] = 0
    except (OSError, ValueError):
        pass
    for dev in devices:
        room = (dev.get("room") or "").strip()
        if room:
            seen[room] = seen.get(room, 0) + 1
    try:
        grouped = (overrides.load().get("rooms") or {})
        for room in grouped.values():
            seen.setdefault(room, seen.get(room, 0))
    except Exception:
        pass
    return [{"room": room, "devices": count} for room, count in sorted(seen.items())]


PAGE = """<!doctype html>
<html lang="de"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Geräteverwaltung</title>
<style>
:root{--bg:#111418;--card:#1a1f26;--line:#2a323c;--txt:#e6edf3;--dim:#8b98a5;--ok:#3fb950;--warn:#d29922;--bad:#f85149;--acc:#1f6feb}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--txt);font:15px/1.5 system-ui,-apple-system,Segoe UI,sans-serif}
header{position:sticky;top:0;background:#0d1117;border-bottom:1px solid var(--line);padding:14px 18px;z-index:5;display:flex;gap:14px;align-items:center;flex-wrap:wrap}
h1{font-size:17px;margin:0;font-weight:600}
input,select,button{font:inherit;background:var(--card);color:var(--txt);border:1px solid var(--line);border-radius:7px;padding:7px 10px}
button{cursor:pointer}
button:hover{border-color:var(--acc)}
button.primary{background:var(--acc);border-color:var(--acc);color:#fff}
main{padding:16px 18px 60px;max-width:1150px;margin:0 auto}
.stats{display:flex;gap:18px;flex-wrap:wrap;color:var(--dim);font-size:13px;margin-bottom:14px}
.stats b{color:var(--txt)}
.roomgroup{margin:20px 0}
.roomhead{display:flex;align-items:center;gap:10px;margin:0 0 8px;font-size:13px;text-transform:uppercase;letter-spacing:.06em;color:var(--dim)}
.roomhead .n{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:1px 8px;font-size:12px}
table{width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line);border-radius:9px;overflow:hidden}
th{text-align:left;font-size:12px;color:var(--dim);font-weight:600;padding:9px 11px;border-bottom:1px solid var(--line);white-space:nowrap}
td{padding:8px 11px;border-bottom:1px solid #222831;vertical-align:middle}
tr:last-child td{border-bottom:none}
tr:hover td{background:#1e242c}
.name{font-weight:500}
.base{color:var(--dim);font-size:12px}
.badge{display:inline-block;font-size:11px;padding:1px 7px;border-radius:9px;border:1px solid var(--line);color:var(--dim)}
.badge.local{color:var(--ok);border-color:#1f4d2b}
.badge.cloud{color:var(--warn);border-color:#4d3f1f}
.badge.broken{color:var(--bad);border-color:#5d2626}
.state{font-variant-numeric:tabular-nums}
.on{color:var(--ok)} .off{color:var(--dim)} .unknown{color:#5c6773}
.dp{font-family:ui-monospace,monospace;font-size:11px;color:var(--dim);word-break:break-all;max-width:330px}
input.inline{width:100%;min-width:130px;background:#141920}
input.dpin{width:100%;min-width:250px;background:#141920;font-family:ui-monospace,monospace;font-size:11px;padding:4px 7px}
input.dpin.changed{border-color:var(--warn)}
input.inline.changed{border-color:var(--warn);background:#221c10}
tr.dirty td{background:#1d1a12}
button#savebtn:disabled{opacity:.45;cursor:default}
button.del{color:var(--bad);border-color:#5d2626}
button.undo{color:var(--ok);border-color:#1f4d2b}
tr.gone td{opacity:.45}
tr.gone td input{text-decoration:line-through}
.grid{display:flex;gap:10px;flex-wrap:wrap;align-items:center}
#msg{position:fixed;bottom:16px;left:50%;transform:translateX(-50%);background:var(--card);border:1px solid var(--line);padding:9px 16px;border-radius:9px;display:none;z-index:9}
#msg.ok{border-color:#1f4d2b} #msg.bad{border-color:#5d2626}
pre{background:var(--card);border:1px solid var(--line);border-radius:9px;padding:12px;overflow:auto;font-size:12.5px;margin:0}
</style></head><body>
<header>
  <h1>Geräteverwaltung</h1>
  <input id="filter" placeholder="Gerät filtern …" style="min-width:210px">
  <button id="savebtn" class="primary" onclick="saveAll()" disabled>Speichern</button>
  <button onclick="load()">Neu laden</button>
  <span id="dirty" style="color:var(--warn);font-size:12px"></span>
  <span id="meta" style="color:var(--dim);font-size:12px"></span>
</header>
<main>
  <div class="stats" id="stats"></div>

  <div class="roomgroup">
    <div class="roomhead">Satz testen <span class="badge">schaltet nichts</span></div>
    <div class="grid">
      <input id="sentence" placeholder='z. B. "Schalte Monitor aus"' style="flex:1;min-width:260px" value="Schalte Monitor aus">
      <button class="primary" onclick="testSentence()">Auflösen</button>
    </div>
    <pre id="result" style="margin-top:10px;display:none"></pre>
  </div>

  <datalist id="raumliste"></datalist>
  <div id="rooms"></div>
</main>
<div id="msg"></div>
<script>
let DATA = [];
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));

function toast(text, kind) {
  const el = document.getElementById("msg");
  el.textContent = text;
  el.className = kind || "";
  el.style.display = "block";
  clearTimeout(el._t);
  el._t = setTimeout(() => { el.style.display = "none"; }, 2600);
}

function stateText(v, readable) {
  if (v === true) return '<span class="state on">an</span>';
  if (v === false) return '<span class="state off">aus</span>';
  if (v === null || v === undefined) {
    return readable === false
      ? '<span class="state unknown" title="Reiner Befehlsdatenpunkt ohne Rückkanal">kein Rückkanal</span>'
      : '<span class="state unknown">–</span>';
  }
  return '<span class="state">' + esc(v) + '</span>';
}

function row(d) {
  const key = d.id || d.name;
  const badge = d.route === "local"
    ? '<span class="badge local">lokal</span>'
    : '<span class="badge cloud">Alexa-Cloud</span>';
  const broken = (d.broken && d.broken.length)
    ? ' <span class="badge broken" title="' + esc(d.broken.join("\\n")) + '">' + d.broken.length + ' defekt</span>'
    : "";
  const base = d.baseName && d.baseName !== d.name
    ? '<div class="base">vorher: ' + esc(d.baseName) + '</div>' : "";
  const room = d.room || "";
  return '<tr data-k="' + esc(key) + '" class="' + (d.deleted ? "gone" : "") + '">'
    + '<td><input class="inline" data-f="name" data-v="' + esc(d.name)
    + '" value="' + esc(d.name) + '" oninput="touch(this)">' + base + '</td>'
    + '<td><input class="inline" data-f="room" data-v="' + esc(room)
    + '" value="' + esc(room) + '" placeholder="—" list="raumliste"'
    + ' oninput="touch(this)" style="min-width:105px"></td>'
    + '<td>' + badge + broken + '</td>'
    + '<td>' + stateText(d.state, d.readable) + '</td>'
    + '<td class="dp"><input class="dpin" data-f="target" data-v="' + esc(d.target || "")
    + '" data-base="' + esc(d.autoTarget || d.target || "")
    + '" value="' + esc(d.target || "") + '" placeholder="Datenpunkt" oninput="touch(this)" title="'
    + (d.targetOverridden ? "vom Eigentümer vorgegeben" : "automatisch ermittelt")
    + '"></td>'
    + '<td>' + (d.targetOverridden
        ? '<button onclick="clearTarget(this)" title="Vorgabe entfernen">×</button>'
        : '') + '</td>'
    + '<td>' + (d.deleted
        ? '<button class="undo" onclick="restoreDevice(this)" title="wieder einblenden">einblenden</button>'
        : '<button class="del" onclick="deleteDevice(this)" title="ausblenden">löschen</button>')
    + '</td>'
    + '</tr>';
}

// Vorschlagsliste fuer die Raum-Spalte: bestehende Raeume aus rooms.json
// plus alles, was schon in der Liste eingetragen ist. Freie Eingabe bleibt
// moeglich - datalist schlaegt nur vor, es zwingt nichts.
function raumVorschlaege() {
  const namen = new Set();
  for (const d of DATA) {
    const r = (d.room || "").trim();
    if (r) namen.add(r);
  }
  const liste = document.getElementById("raumliste");
  if (!liste) return;
  liste.innerHTML = [...namen]
    .sort((a, b) => a.localeCompare(b, "de"))
    .map(r => '<option value="' + esc(r) + '">')
    .join("");
}

function render() {
  const q = document.getElementById("filter").value.trim().toLowerCase();
  const shown = DATA.filter(d => !q
    || (d.name || "").toLowerCase().includes(q)
    || (d.room || "").toLowerCase().includes(q)
    || (d.target || "").toLowerCase().includes(q));

  const groups = new Map();
  for (const d of shown) {
    const k = (d.room || "").trim() || "Ohne Raum";
    if (!groups.has(k)) groups.set(k, []);
    groups.get(k).push(d);
  }
  const order = [...groups.keys()].sort((a, b) =>
    a === "Ohne Raum" ? 1 : b === "Ohne Raum" ? -1 : a.localeCompare(b));

  document.getElementById("rooms").innerHTML = order.map(room => {
    const list = groups.get(room);
    return '<div class="roomgroup"><div class="roomhead">' + esc(room)
      + ' <span class="n">' + list.length + '</span></div>'
      + '<table><thead><tr><th>Sprechname</th><th>Raum</th><th>Weg</th><th>Zustand</th><th>Datenpunkt</th><th></th><th></th></tr></thead><tbody>'
      + list.map(row).join("") + '</tbody></table></div>';
  }).join("");

  raumVorschlaege();

  const local = DATA.filter(d => d.route === "local").length;
  const broken = DATA.filter(d => d.broken && d.broken.length).length;
  const weg = DATA.filter(d => d.deleted).length;
  document.getElementById("stats").innerHTML =
    '<span><b>' + (DATA.length - weg) + '</b> Geräte</span>'
    + '<span><b>' + local + '</b> lokal schaltbar</span>'
    + '<span><b>' + (DATA.length - weg - local) + '</b> nur über Alexa</span>'
    + '<span><b>' + broken + '</b> mit defektem Ziel</span>'
    + (weg ? '<span><b>' + weg + '</b> ausgeblendet</span>' : '');
}

async function load() {
  document.getElementById("meta").textContent = "lädt …";
  try {
    const res = await fetch("/api/devices");
    const data = await res.json();
    DATA = data.devices || [];
    render();
    document.getElementById("meta").textContent =
      new Date().toLocaleTimeString("de-DE") + " · " + (data.rooms || []).length + " Räume";
  } catch (e) {
    document.getElementById("meta").textContent = "Fehler beim Laden";
    toast("Laden fehlgeschlagen", "bad");
  }
}

async function post(url, body) {
  const res = await fetch(url, {
    method: "POST",
    headers: {"Content-Type": "application/json"},
    body: JSON.stringify(body)
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok || data.ok === false) throw new Error(data.error || "Fehler");
  return data;
}

// ---- Änderungen sammeln, erst auf Knopfdruck schreiben ----
// Jedes Feld merkt sich seinen Ausgangswert in data-v. Weicht der Inhalt ab,
// wird die Zeile markiert; gespeichert wird ausschliesslich in saveAll().
function touch(input) {
  const tr = input.closest("tr");
  const geaendert = input.value !== input.dataset.v;
  input.classList.toggle("changed", geaendert);
  tr.classList.toggle("dirty", !!tr.querySelector("input.changed"));
  updateDirty();
}

function sammle() {
  const out = [];
  document.querySelectorAll("tr.dirty").forEach(tr => {
    const eintrag = {id: tr.dataset.k};
    let hat = false;
    tr.querySelectorAll("input[data-f]").forEach(inp => {
      const feld = inp.dataset.f;
      const alt = inp.dataset.v || "";
      if (inp.value !== alt) {
        eintrag[feld] = inp.value;
        hat = true;
      }
    });
    if (hat) out.push(eintrag);
  });
  return out;
}

function updateDirty() {
  const n = document.querySelectorAll("tr.dirty").length;
  const knopf = document.getElementById("savebtn");
  const hinweis = document.getElementById("dirty");
  knopf.disabled = n === 0;
  hinweis.textContent = n === 0 ? "" : n + " Zeile(n) geändert";
}

async function saveAll() {
  const changes = sammle();
  if (!changes.length) {
    toast("Nichts zu speichern", "ok");
    return;
  }
  const knopf = document.getElementById("savebtn");
  knopf.disabled = true;
  knopf.textContent = "speichert …";
  try {
    const res = await post("/api/save-batch", {changes});
    toast(res.saved + " Änderung(en) gespeichert", "ok");
  } catch (e) {
    toast("Nicht gespeichert: " + e.message, "bad");
  } finally {
    knopf.textContent = "Speichern";
    await load();
  }
}

function clearTarget(button) {
  // Vorgabe entfernen heisst: Feld auf den automatisch ermittelten Wert
  // zurueckzusetzen. Geschrieben wird erst beim Speichern.
  const tr = button.closest("tr");
  const feld = tr.querySelector('input[data-f="target"]');
  if (!feld) return;
  feld.value = feld.dataset.base || "";
  feld.classList.add("changed");
  tr.classList.add("dirty");
  updateDirty();
}

// Löschen heißt hier: aus der Liste nehmen. In ioBroker wird NICHTS
// angetastet - das Gerät bleibt dort vollständig bestehen.
async function deleteDevice(button) {
  const tr = button.closest("tr");
  const key = tr.dataset.k;
  const nameFeld = tr.querySelector('input[data-f="name"]');
  const name = nameFeld ? nameFeld.value : key;
  if (!confirm("„" + name + "“ aus der Liste nehmen?\\n\\n" +
               "Das Gerät wird danach nicht mehr geschaltet und nicht mehr " +
               "erkannt. In ioBroker bleibt es unverändert bestehen und kann " +
               "hier jederzeit wieder eingeblendet werden.")) {
    return;
  }
  try {
    await post("/api/delete", {id: key, deleted: true});
    toast("„" + name + "“ aus der Liste genommen", "ok");
    await load();
  } catch (e) {
    toast("Nicht entfernt: " + e.message, "bad");
  }
}

async function restoreDevice(button) {
  const tr = button.closest("tr");
  const key = tr.dataset.k;
  const nameFeld = tr.querySelector('input[data-f="name"]');
  const name = nameFeld ? nameFeld.value : key;
  if (!confirm("„" + name + "“ wieder einblenden?")) return;
  try {
    await post("/api/delete", {id: key, deleted: false});
    toast("„" + name + "“ wieder da", "ok");
    await load();
  } catch (e) {
    toast("Nicht eingeblendet: " + e.message, "bad");
  }
}

async function testSentence() {
  const text = document.getElementById("sentence").value;
  const out = document.getElementById("result");
  out.style.display = "block";
  out.textContent = "…";
  try {
    const data = await post("/api/test", {text});
    out.textContent = JSON.stringify(data.result, null, 2);
  } catch (e) {
    out.textContent = "Fehler: " + e.message;
  }
}

document.getElementById("filter").addEventListener("input", render);
load();
</script></body></html>
"""


class Handler(BaseHTTPRequestHandler):
    server_version = "Geraeteverwaltung/1.0"

    def log_message(self, fmt, *args):  # leiser: nur echte Fehler
        pass

    def _send(self, code: int, body: bytes, kind: str = "application/json"):
        self.send_response(code)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, payload):
        self._send(code, json.dumps(payload, ensure_ascii=False).encode())

    def do_GET(self):
        path = urllib.parse.urlparse(self.path).path
        if path in ("/", "/index.html"):
            self._send(200, PAGE.encode(), "text/html; charset=utf-8")
            return
        if path == "/api/devices":
            try:
                devices = collect()
                self._json(200, {"devices": devices, "rooms": room_list(devices)})
            except Exception as exc:  # noqa: BLE001
                self._json(500, {"ok": False, "error": repr(exc)})
            return
        self._json(404, {"ok": False, "error": "unbekannt"})

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            body = json.loads(raw.decode() or "{}")
        except ValueError:
            self._json(400, {"ok": False, "error": "ungueltiges JSON"})
            return

        try:
            if path == "/api/rename":
                overrides.set_name(body.get("id"), body.get("name"))
                self._json(200, {"ok": True})
                return
            if path == "/api/room":
                overrides.set_room(body.get("id"), body.get("room"))
                self._json(200, {"ok": True})
                return
            if path == "/api/save-batch":
                changes = body.get("changes") or []
                if not isinstance(changes, list):
                    self._json(400, {"ok": False, "error": "changes muss eine Liste sein"})
                    return
                saved = overrides.save_batch(changes)
                self._json(200, {"ok": True, "saved": saved})
                return
            if path == "/api/delete":
                overrides.set_deleted(body.get("id"), bool(body.get("deleted")))
                self._json(200, {"ok": True})
                return
            if path in ("/api/target", "/api/change-target"):
                overrides.set_target(body.get("id"), body.get("datapoint"),
                                     body.get("cap") or "")
                self._json(200, {"ok": True})
                return
            if path == "/api/test":
                text = " ".join(str(body.get("text") or "").split())
                if not text:
                    self._json(400, {"ok": False, "error": "kein Satz"})
                    return
                result = control.handle(text, dry_run=True, prefer_local=True)
                self._json(200, {"ok": True, "result": result})
                return
        except Exception as exc:  # noqa: BLE001
            self._json(500, {"ok": False, "error": repr(exc)})
            return
        self._json(404, {"ok": False, "error": "unbekannt"})


def main() -> int:
    host = os.environ.get("PANEL_HOST", "0.0.0.0")
    port = int(os.environ.get("PANEL_PORT", "8088"))
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"Geraeteverwaltung auf http://{host}:{port}  (ioBroker: {BASE})", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
