"""Befehl ausfuehren: Datenpunkt schreiben und Ergebnis ehrlich melden.

Der Schreibweg ist derselbe, den der alexa2-Adapter selbst nutzt: die
Datenpunkte unter Smart-Home-Devices sind write:true, und der Adapter
uebersetzt den Wert in den Alexa-Steuerbefehl (turnOn/turnOff/setBrightness).
HTTP 200 belegt nur die Annahme durch ioBroker, nicht die physische Wirkung.
Deshalb wird der Zustand nachgelesen, wo der Adapter ihn zurueckmeldet.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

import catalog
import fuzzy
import localmap
import overrides
import resolver
import rooms

BASE = catalog.BASE
READBACK_TIMEOUT = 6.0
POLL_INTERVAL = 0.15


def _get_state(datapoint):
    url = f"{BASE}/get/{urllib.parse.quote(datapoint, safe='')}/"
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            return json.loads(response.read().decode())
    except Exception:
        return None


def is_group_target(datapoint):
    """Zigbee-Gruppenadresse (zigbee.0.group_N.state)?

    Gruppen MELDEN zurueck, aber nur bei echter Wertaenderung. Ein Schreiben
    desselben Werts erzeugt kein Ereignis, deshalb bleibt `lc` dann alt.
    Belegt 2026-10-27: group_1.state stand 230 h auf true, weil der Wert nie
    wechselte; ein Schreiben von false aktualisierte ihn sofort
    (val=false, ack=true, lc frisch).

    WICHTIG: Der Gruppenbefehl schaltet die Lampen physisch, aktualisiert aber
    die Einzel-Datenpunkte NICHT. Nach einem Gruppenbefehl ist der Zustand der
    Einzellampen unbrauchbar (belegt: alle aus, Einzelwerte zeigten weiter true).
    """
    parts = datapoint.split(".")
    return (len(parts) >= 4 and parts[0] == "zigbee" and parts[1] == "0"
            and parts[2].startswith("group_"))


def confirm_write(datapoint, value, timeout=4.0):
    """Hat der Adapter den Schreibvorgang wirklich bestaetigt?

    Entscheidend ist `ack`: nur dann hat der zigbee-Adapter den Wert vom Gerät
    zurückgemeldet. HTTP 200 allein belegt NICHTS — simple-api setzt den Wert
    optimistisch mit ack=false, auch wenn das Gerät offline ist.
    Belegt 2026-10-27: Schreiben auf ein als offline gemeldetes Gerät liefert
    HTTP 200 und val=true, aber dauerhaft ack=false und
    from=system.adapter.simple-api.0. Das Geraet hat nie geschaltet.

    Gibt ("bestaetigt" | "offen" | "widerspruch") zurueck.
    """
    if is_group_target(datapoint):
        # Gruppen melden eine Wertaenderung NUR dann zurueck, wenn sie den Wert
        # selbst mitbekommen. Gemessen 2026-10-27: Schreiben auf group_1.state
        # lieferte nach 1125 ms weder ack noch frisches lc, obwohl die Lampen
        # schalteten. Warten bringt dort nichts, kostet aber Sekunden.
        # Deshalb: als "gesendet" melden, nicht als "bestaetigt".
        return "gesendet"
    started = time.time()
    while time.time() - started < timeout:
        obj = _get_state(datapoint)
        if obj:
            val = obj.get("val")
            ack = obj.get("ack")
            lc = obj.get("lc") or 0
            # Ein alter Wert mit ack=true ist KEINE Bestaetigung. Genau das
            # passiert bei zigbee-Gruppen: group_1.state stand 230 h lang auf
            # true, weil der Adapter Gruppenbefehle nicht zurueckmeldet
            # (stateupdate = "off", belegt 2026-10-27).
            fresh = (lc / 1000.0) >= started - 1.0
            if ack and val == value and fresh:
                return "bestaetigt"
            if val == value and not fresh:
                if not ack:
                    # val stimmt, aber niemand hat es bestaetigt: typisch fuer
                    # ein Schreiben auf ein offline gemeldetes Geraet. Der Wert
                    # steht da, das Geraet hat ihn aber nie quittiert.
                    return "unbestaetigt"
                # Der Wert war bereits gesetzt und ist vom Adapter bestaetigt.
                return "bereits-gesetzt"
            if ack and val != value and fresh:
                return "widerspruch"
        time.sleep(POLL_INTERVAL)
    return "offen"


def is_available(datapoint):
    """Meldet der zigbee-Adapter das Geraet als erreichbar?

    Gibt True/False/None zurueck (None = keine Aussage moeglich). Der Adapter
    meldet Einzelgeraete mitunter als offline, waehrend sie ueber eine Gruppe
    erreichbar bleiben (belegt 2026-10-27). Deshalb wird hier nur gewarnt und
    nicht umgeleitet — der Nutzer hat das Ziel genannt, nicht wir.
    """
    if not datapoint.startswith("zigbee.0."):
        return None
    dev = ".".join(datapoint.split(".")[:3])
    value = _read_value(f"{dev}.available")
    return None if value is None else bool(value)


def _read_value(datapoint):
    obj = _get_state(datapoint)
    if not obj:
        return None
    common = obj.get("common", {}) or {}
    if not common.get("read"):
        return None          # write-only: kein Istwert vorhanden
    return obj.get("val")


def write_state(datapoint, value, wait_ack=False, timeout=8.0):
    """Schreibt den Wert. Gibt (ok, http_status, dauer_sekunden, detail)."""
    encoded = urllib.parse.quote(datapoint, safe="")
    if isinstance(value, bool):
        text = "true" if value else "false"
    elif isinstance(value, float) and value.is_integer():
        text = str(int(value))
    else:
        text = str(value)
    url = f"{BASE}/set/{encoded}?value={urllib.parse.quote(text, safe='')}"
    if wait_ack:
        url += "&wait=3000"
    started = time.time()
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            body = response.read(200).decode(errors="replace")
            status = response.status
    except urllib.error.HTTPError as exc:
        return False, exc.code, time.time() - started, exc.read(200).decode(errors="replace")
    except Exception as exc:
        return False, None, time.time() - started, repr(exc)
    return 200 <= status < 300, status, time.time() - started, body.strip()


def _wait_for_ack(datapoint, value, timeout=READBACK_TIMEOUT):
    deadline = time.time() + timeout
    while time.time() < deadline:
        obj = _get_state(datapoint)
        if obj and obj.get("ack") and obj.get("val") == value:
            return True
        time.sleep(POLL_INTERVAL)
    return False


def current_percent(datapoint_id, writable):
    """Ist-Helligkeit, falls der Adapter sie zurueckmeldet."""
    for cap in ("brightness", "percentage", "color-brightness"):
        if cap in writable:
            return _read_value(f"{datapoint_id}.{cap}")
    return None


def execute(resolution, dry_run=False):
    if not resolution.ok:
        return {"ok": False, "reason": resolution.reason,
                "candidates": resolution.candidates}, 0.0

    # Vorgegebenes Ziel schlaegt den abgeleiteten Datenpunkt. Betrifft den
    # Alexa-Zweig (device_id + state); der lokale Zweig wird in
    # local_target() behandelt.
    vorgabe = overrides.target_for(resolution.device_id, resolution.device)
    dp = vorgabe["datapoint"] if vorgabe else f"{resolution.device_id}.{resolution.state}"
    value = resolution.value

    if resolution.relative is not None:
        actual = current_percent(resolution.device_id, _caps(resolution.device_id))
        base = actual if isinstance(actual, (int, float)) else 100 if resolution.relative > 0 else 0
        value = max(0, min(100, base + resolution.relative))

    if dry_run:
        return {"ok": True, "dry_run": True, "datapoint": dp, "value": value,
                "device": resolution.device, "action": resolution.action}, 0.0

    ok, status, elapsed, detail = write_state(dp, value)
    result = {"ok": ok, "datapoint": dp, "value": value, "http": status,
              "seconds": round(elapsed, 3), "device": resolution.device,
              "action": resolution.action, "detail": detail[:200]}
    if ok:
        result["ack"] = _wait_for_ack(dp, value) if resolution.state in ("powerState", "active") else None
    return result, elapsed


def _caps(device_id):
    data = catalog.load()
    for dev in data["devices"]:
        if dev["id"] == device_id:
            return dev["writable"]
    return []


# Welche lokale Faehigkeit traegt welche Aktion
LOCAL_CAP_FOR = {
    "on": "onoff",
    "off": "onoff",
    "toggle": "onoff",
    "percent": "brightness",
}


def local_target(device_name, action, value=None, ident=None):
    """Lokales Ziel aus dem iot-/alias-Abgleich, falls belegt.

    Gibt (datapoint, value) zurueck oder None. Es wird nur zurueckgegriffen,
    wenn der NAME exakt dem smartName entspricht — kein Fuzzy-Match auf
    Datenpunkte, sonst schaltet ein aehnlicher Name das falsche Geraet.

    `ident` ist die Kennung, unter der eine Eigentuemer-Vorgabe gespeichert
    ist (Datenpunkt-id). Ohne sie wird eine Vorgabe uebersehen, wenn sie unter
    der id statt unter dem Sprechnamen liegt (belegt 27.10.2026).
    """
    # Vom Eigentuemer vorgegebenes Ziel hat Vorrang vor dem abgeleiteten.
    # Gedacht fuer defekte Aliasse und falsch erkannte Aktoren; ohne diese
    # Pruefung waere die Angabe im Panel reine Anzeige.
    vorgabe = overrides.target_for(ident, device_name)
    if vorgabe:
        ziel = vorgabe["datapoint"]
        cap = (vorgabe.get("cap") or "").strip()
        if action == "temperature":
            return (ziel, value) if value is not None else None
        if cap in ("brightness", "percentage", "color-brightness"):
            return (ziel, value) if value is not None else None
        if action == "on":
            return ziel, True
        if action == "off":
            return ziel, False
        if action == "toggle":
            current = _read_value(ziel)
            if current is None:
                return None
            return ziel, not bool(current)
        if value is not None:
            return ziel, value

    key = localmap.normalize(device_name)
    for dev in localmap.load()["devices"]:
        if dev["key"] != key:
            continue
        caps = dev.get("caps") or {}
        if action == "temperature":
            target = caps.get("setpoint")
            return (target, value) if target else None
        cap = LOCAL_CAP_FOR.get(action)
        if not cap:
            return None
        target = caps.get(cap)
        if not target:
            return None
        if action == "on":
            return target, True
        if action == "off":
            return target, False
        if action == "toggle":
            current = _read_value(target)
            if current is None:
                return None          # ohne Istwert kein sicheres Umschalten
            return target, not bool(current)
        return target, value
    return None


# Uebersetzung lokale Faehigkeit -> Wortschatz des Resolvers
_CAP_TO_RESOLVER = {
    "onoff": "powerState",
    "brightness": "brightness",
    "setpoint": "targetSetpoint",
}


def local_devices():
    """Geraeteliste fuer den Resolver, aufgebaut aus dem lokalen Abgleich.

    Notwendig, weil alexa2 denselben Namen mehrfach fuehren kann ("Heizung"
    zweimal: Einzelgeraet und Gruppe), waehrend der smartName nur einmal
    existiert. Lokal ist die Zuordnung dann eindeutig, in der Cloud nicht.
    """
    out = []
    for dev in localmap.load()["devices"]:
        writable = sorted({_CAP_TO_RESOLVER[c] for c in dev["caps"] if c in _CAP_TO_RESOLVER})
        if not writable:
            continue
        out.append({"id": dev["name"], "name": dev["name"], "writable": writable})
    return out


_VOCAB = None


def vocab():
    """Vokabular fuer die STT-Korrektur: Raeume und Geraetenamen aus den
    echten Objekten, nicht erfunden."""
    global _VOCAB
    if _VOCAB is None:
        words = set(fuzzy.KNOWN_CONFUSIONS.values())
        for dev in localmap.load()["devices"]:
            words.add(dev["name"])
            words.update(dev["name"].split())
        for dev in catalog.load()["devices"]:
            words.add(dev["name"])
            words.update(dev["name"].split())
        _VOCAB = fuzzy.Vocabulary(words)
    return _VOCAB


def room_batch(text):
    """Nennt der Satz einen Raum mit mehreren Lampen, alle schalten.

    Belegt: "Wohnzimmer" besteht laut Bewegungsskript aus 5 Lampen (WLED,
    Yeelight-Decke, 5-m-Zigbee-Streifen, beide Deckenstrahler). "Wohnzimmer an"
    schaltet alle. Die Automatik des Bewegungsskripts wird dabei NICHT
    ausgehebelt — sie laeuft unabhaengig weiter, wie vom Eigentuemer gewuenscht.
    """
    intent = resolver.parse_action(text)
    if intent.action not in ("on", "off"):
        return None
    room = resolver.room_in(text)
    if not room:
        return None
    # Nur wenn der Satz keinen konkreten Geraetenamen nennt.
    # local_devices() statt localmap-Rohdaten: die Rohdaten haben kein Feld
    # `id`, match_devices greift aber darauf zu (KeyError 'id', belegt
    # 27.10.2026 bei "arbeitszimmer Heizung an").
    devices = local_devices()
    if not devices:
        matches = []
    else:
        matches = resolver.match_devices(text, devices)
    if matches and matches[0].score >= 100:
        return None
    entry = rooms.lights_for(room)
    if not entry:
        return None
    # Gruppe bevorzugen: der zigbee-Adapter meldet Einzelgeraete mitunter als
    # offline (available=false) und schaltet sie dann nicht, waehrend dieselbe
    # Lampe ueber die Gruppe erreichbar bleibt. Belegt 2026-10-27 an
    # 842e14fffe19daf3 (avail=false, group_1 schaltet trotzdem).
    targets = entry.get("preferred") or entry["lights"]
    # Ein einzelnes Ziel ohne Gruppe uebernimmt der normale Geraetepfad.
    if len(targets) < 2 and not entry.get("group"):
        return None
    value = intent.action == "on"
    return {"room": entry["room"], "targets": targets, "value": value,
            "group": entry.get("group")}


def handle(text, dry_run=False, prefer_local=False):
    if prefer_local:
        batch = room_batch(text)
        if batch:
            result = {"ok": True, "route": "local", "utterance": text,
                      "room": batch["room"], "device": batch["room"],
                      "action": "on" if batch["value"] else "off",
                      "targets": len(batch["targets"]),
                      "group": batch.get("group")}
            if dry_run:
                result["datapoints"] = batch["targets"]
                result["value"] = batch["value"]
                return result
            written, failed = [], []
            for dp in batch["targets"]:
                ok, status, elapsed, detail = write_state(dp, batch["value"])
                entry = {"datapoint": dp, "http": status}
                if ok:
                    entry["confirm"] = confirm_write(dp, batch["value"])
                (written if ok else failed).append(entry)
            result["written"] = written
            result["failed"] = failed
            result["ok"] = not failed
            offline = [w["datapoint"] for w in written if is_available(w["datapoint"]) is False]
            if offline:
                result["offline_warning"] = offline
            # Nach einem Gruppenbefehl bleiben die Einzel-Datenpunkte stehen.
            # Wer danach den Zustand einer Lampe liest, bekommt einen falschen
            # Wert. Das wird ausgewiesen, statt es zu verschweigen.
            if any(is_group_target(w["datapoint"]) for w in written):
                result["stale_warning"] = (
                    "Gruppenbefehl: die Einzel-Datenpunkte der Lampen werden "
                    "vom zigbee-Adapter NICHT mitaktualisiert und zeigen "
                    "weiterhin den alten Zustand."
                )
            return result
    devices = catalog.load()["devices"]
    resolution = None
    if prefer_local:
        # Erst lokal aufloesen: dort gibt es keine Cloud-Mehrdeutigkeit.
        local_devs = local_devices()
        local_res = resolver.resolve(text, local_devs)
        if local_res.ok:
            hit = local_target(local_res.device, local_res.action, local_res.value)
            if hit:
                resolution = local_res
    if resolution is None:
        resolution = resolver.resolve(text, devices)
    result, _ = execute(resolution, dry_run=True)   # erst aufloesen, nicht schreiben
    result["utterance"] = text
    if not resolution.ok:
        # STT-Verhoerer korrigieren und EINMAL erneut versuchen.
        # Kein zweiter Versuch bei bereits erfolgreicher Aufloesung: dort
        # wuerde die Korrektur nur den Treffer verwischen.
        fixed, changes = fuzzy.correct_sentence(text, vocab())
        if changes:
            result["corrected"] = [{"from": a, "to": b, "ratio": round(r, 2)}
                                   for a, b, r in changes]
            retry = None
            if prefer_local:
                local_res = resolver.resolve(fixed, local_devices())
                if local_res.ok and local_target(local_res.device, local_res.action,
                                                  local_res.value):
                    retry = local_res
            if retry is None:
                retry = resolver.resolve(fixed, devices)
            if retry.ok:
                # Wichtig: resolution ersetzen, damit das lokale Routing
                # unten den KORRIGIERTEN Treffer benutzt, nicht den Fehlschlag.
                resolution = retry
                result["corrected_utterance"] = fixed
                result, _ = execute(resolution, dry_run=True)
                result["utterance"] = text
                result["corrected_utterance"] = fixed
    # ------------------------------------------------------------------
    # Zweite Stufe: HassIL (Home Assistant Intent Language)
    #
    # Der eigene Resolver deckt an/aus und Prozent ab. Farbe, Farbtemperatur,
    # "maximum"/"minimum" und Formulierungen wie "stelle die Helligkeit im
    # Arbeitszimmer auf 50 prozent" kennt er nicht - die landeten bisher beim
    # Agenten und brauchten 7-12 s. HassIL bringt fertige deutsche Vorlagen.
    #
    # Bewusst NACH dem eigenen Weg: der ist erprobt, kennt Raum-Gruppen und
    # bevorzugte Datenpunkte. HassIL springt nur ein, wenn dort nichts
    # gefunden wurde.
    # ------------------------------------------------------------------
    # HassIL auch dann fragen, wenn der eigene Weg zwar "gefunden" meldet,
    # aber keinen brauchbaren Wert hat. Belegt 27.10.2026:
    #   "Licht Arbeitszimmer auf maximum"  -> action=percent, value=None
    #   "Licht Arbeitszimmer auf warmweiß" -> action=percent, value=None
    #   "dimme das Licht in der Küche auf 20" -> action=relative, value=True
    # Der eigene Resolver kennt Woerter wie "maximum" oder "warmweiß" nicht
    # und relative Angaben ohne Istwert auch nicht. HassIL kennt beides.
    #
    # Der bewaehrte Weg behaelt Vorrang, solange er einen echten Wert hat
    # (z.B. "auf 40 prozent" -> value=40).
    _wertlos = (
        resolution.ok
        and resolution.action in ("percent", "relative")
        and (resolution.value is None or isinstance(resolution.value, bool))
    )
    if not resolution.ok or _wertlos:
        try:
            import hassil_router
            erkannt = hassil_router.parse(text)
            befehl = hassil_router.als_schaltbefehl(erkannt) if erkannt else None
        except Exception as exc:  # noqa: BLE001
            print(f"hassil nicht nutzbar: {exc!r}", flush=True)
            erkannt, befehl = None, None

        if befehl:
            ziel, wert = befehl
            if wert is None:
                befehl = None
        if befehl:
            ziel, wert = befehl
            lokal = localmap._is_local(ziel)
            treffer = {
                "ok": True,
                "engine": "hassil",
                "intent": (erkannt or {}).get("intent"),
                "device": (erkannt or {}).get("name") or (erkannt or {}).get("area") or text,
                "room": (erkannt or {}).get("area"),
                "action": "set",
                "datapoint": ziel,
                "value": wert,
                "route": "local" if lokal else "alexa2-cloud",
                "utterance": text,
            }
            if dry_run:
                return treffer
            ok, status, dauer, detail = write_state(ziel, wert)
            treffer.update({"ok": ok, "http": status,
                            "seconds": round(dauer, 3), "detail": detail[:200]})
            return treffer

    result["route"] = "alexa2-cloud"

    if resolution.ok and prefer_local:
        hit = local_target(resolution.device, resolution.action, resolution.value,
                           ident=resolution.device_id)
        if hit:
            target, value = hit
            # Nicht jedes vorgeschlagene Ziel ist wirklich lokal: eine
            # Eigentuemer-Vorgabe darf auch auf einen Alexa-Kanal zeigen.
            # Dann bleibt die Kennzeichnung "alexa2-cloud", sonst behauptet
            # das Ergebnis einen Cloud-freien Weg, den es nicht gibt.
            if localmap._is_local(target):
                result["route"] = "local"
            else:
                result["alexa2_datapoint"] = result.get("datapoint")
            result["datapoint"] = target
            result["value"] = value

    if not dry_run:
        if not result.get("ok") or "datapoint" not in result:
            return result            # nichts zu schalten, nichts schreiben
        ok, status, elapsed, detail = write_state(result["datapoint"], result["value"])
        result.update({"ok": ok, "http": status, "seconds": round(elapsed, 3),
                       "detail": detail[:200]})
    if resolution.candidates and not result.get("ok"):
        result["candidates"] = resolution.candidates
    return result


def main():
    parser = argparse.ArgumentParser(description="Alexa-Geraet direkt schalten")
    parser.add_argument("utterance", help="gesprochener Satz")
    parser.add_argument("--dry-run", action="store_true", help="nur aufloesen, nichts schreiben")
    parser.add_argument("--local", action="store_true", help="lokales Ziel bevorzugen")
    args = parser.parse_args()
    print(json.dumps(handle(args.utterance, dry_run=args.dry_run,
                            prefer_local=args.local), ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
