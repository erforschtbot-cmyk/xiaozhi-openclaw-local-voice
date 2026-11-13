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
import groups
import hassil_router
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


def _dp_prefix(datapoint: str) -> str:
    """Geräte-Präfix eines Datenpunkts (ohne Endung).

    Nötig, weil ein Ziel je nach Herkunft auf `.state`, `.on`, `.power`,
    `.bri` oder `.SET` enden kann. Das Präfix ist in allen Fällen die
    Geräte-Ebene, unter der Helligkeit/Farbe liegen.
    """
    for suffix in (".state", ".on", ".power", ".bri", ".SET", ".POWER"):
        if datapoint.endswith(suffix):
            return datapoint[: -len(suffix)]
    return datapoint.rsplit(".", 1)[0]


def _dp_existiert(datapoint) -> bool:
    """Gibt es den Datenpunkt in ioBroker wirklich?"""
    if not datapoint:
        return False
    return _get_state(datapoint) is not None


def _istwert_datenpunkt(device_name) -> str | None:
    """Nur lesbarer Istwert eines Geräts aus der localmap (oder None)."""
    key = localmap.normalize(device_name)
    for dev in localmap.load().get("devices") or []:
        if dev.get("key") == key or localmap.normalize(dev.get("name")) == key:
            return (dev.get("caps") or {}).get("temperature")
    return None


def _thermostat_sperre(device, soll_datapoint, ist_datapoint):
    """Sollwert nur schreiben, wenn Ist- UND Sollwert-Datenpunkt existieren.

    Vorgabe des Eigentuemers (2026-11-07): ein Thermostat hat immer zwei
    Werte - einen nur lesbaren Istwert und einen schreibbaren Sollwert. Fehlt
    einer davon, wird NICHT gestellt: sonst schreibt der Sprachweg in einen
    Datenpunkt, dessen Gegenstueck es nicht (mehr) gibt.

    Gibt einen Grund-Text zurueck, wenn gesperrt werden muss, sonst None.
    """
    fehlt = []
    if not _dp_existiert(ist_datapoint):
        fehlt.append(f"Istwert ({ist_datapoint or 'keiner'})")
    if not _dp_existiert(soll_datapoint):
        fehlt.append(f"Sollwert ({soll_datapoint or 'keiner'})")
    if not fehlt:
        return None
    return (f"{device}: nicht gestellt - " + " und ".join(fehlt)
            + " fehlt in ioBroker")


def execute(resolution, dry_run=False):
    if not resolution.ok:
        return {"ok": False, "reason": resolution.reason,
                "candidates": resolution.candidates}, 0.0

    # EIN Weg für alle Geräte - keine Sonderbehandlung nach Herkunft.
    # Reihenfolge: Vorgabe des Eigentümers > abgeleitetes Ziel (localmap) >
    # Katalog-Kanal. Welcher Adapter dahinter steht, ist unerheblich.
    _cap = _CAP_FOR_STATE.get(str(resolution.state or ""), resolution.state)
    hit = local_target(resolution.device, resolution.action, resolution.value,
                       ident=resolution.device_id)
    if hit:
        dp, value = hit
    else:
        vorgabe = overrides.target_for(resolution.device_id, resolution.device, cap=_cap)
        dp = vorgabe["datapoint"] if vorgabe else f"{resolution.device_id}.{resolution.state}"
        value = resolution.value

    if resolution.relative is not None:
        # Ist-Helligkeit am ZIEL lesen, nicht am Gerätenamen: der Name ist
        # bei lokalen Geräten kein Datenpunkt-Präfix.
        prefix = _dp_prefix(dp)
        actual = _read_value(f"{prefix}.brightness")
        if actual is None:
            actual = _read_value(f"{prefix}.percentage")
        base = actual if isinstance(actual, (int, float)) else 100 if resolution.relative > 0 else 0
        value = max(0, min(100, base + resolution.relative))

    if resolution.action == "temperature":
        # Sollwert nur bei vollstaendigem Thermostat: Istwert (nur lesbar)
        # und Sollwert (schreibbar) muessen BEIDE in ioBroker existieren.
        sperre = _thermostat_sperre(resolution.device, dp,
                                    _istwert_datenpunkt(resolution.device))
        if sperre:
            return {"ok": False, "reason": "thermostat-unvollstaendig",
                    "device": resolution.device, "datapoint": dp,
                    "detail": sperre}, 0.0

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
    # Faehigkeit bestimmen: die Aktion sagt, WELCHER Datenpunkt gebraucht wird.
    _cap = LOCAL_CAP_FOR.get(action) or ("setpoint" if action == "temperature" else None)
    vorgabe = overrides.target_for(ident, device_name, cap=_cap)
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
        # Gegen Schlüssel UND aktuellen Namen prüfen. Der Schlüssel wird beim
        # Aufbau aus dem ursprünglichen Namen gebildet; wird ein Gerät später
        # umbenannt (belegt 2026-11-04: "Alle Lampen" -> "Deckenstrahler"),
        # passt er nicht mehr und das Gerät wäre über den Sprachweg nicht mehr
        # auffindbar.
        if dev["key"] != key and localmap.normalize(dev.get("name")) != key:
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


# Alexa-Faehigkeitsname -> unsere Kurzform. Noetig, damit eine Vorgabe fuer
# "color" auch greift, wenn der Alexa-Zweig von "colorName" spricht.
_CAP_FOR_STATE = {
    "powerState": "onoff",
    "active": "onoff",
    "brightness": "brightness",
    "percentage": "brightness",
    "colorName": "color",
    "colorTemperatureName": "colortemp",
    "targetSetpoint": "setpoint",
}


# Uebersetzung lokale Faehigkeit -> Wortschatz des Resolvers
_CAP_TO_RESOLVER = {
    "onoff": "powerState",
    "brightness": "brightness",
    "setpoint": "targetSetpoint",
}


def name_zu_datenpunkt(datapoint):
    """Sprechname zu einem Datenpunkt - für die Antwort nach dem Schalten.

    Der Name kommt aus den Daten, nicht aus dem Satz: HassIL legt bei Farbe
    und Weisston den Rest der Äußerung als `floor` ab (belegt 2026-11-04:
    „Deckenstrahler warmes Weiß“ -> floor=„Deckenstrahler warmes“). Ohne diese
    Rückabbildung stünde der halbe Satz als Gerätename in der Sprachantwort.

    Verglichen wird zuerst exakt, dann über das Geräte-Präfix, damit auch
    `.colortemp`/`.brightness` den Gerätenamen finden.
    """
    if not datapoint:
        return None
    dp = str(datapoint)
    prefix = _dp_prefix(dp)
    # Endet der Datenpunkt auf eine Faehigkeit, ist der Praefix das Geraet.
    # Sonst ist der Datenpunkt SELBST das Geraet - dann darf nicht auf Praefix
    # verglichen werden. Belegt 2026-11-04: "Meine_Geraete.0.Beamer" bekam den
    # Praefix "Meine_Geraete.0" und damit den Namen "Airwick".
    _CAP_ENDUNGEN = (".state", ".on", ".power", ".bri", ".SET", ".POWER",
                     ".brightness", ".percentage", ".color", ".colortemp",
                     ".hex_color", ".active", ".powerState", ".dimmer")
    ist_faehigkeit = dp.lower().endswith(_CAP_ENDUNGEN)

    def passt(werte):
        werte = [str(v) for v in werte if v]
        if dp in werte:
            return True
        return ist_faehigkeit and prefix in {_dp_prefix(v) for v in werte}

    try:
        for dev in localmap.load().get("devices") or []:
            if passt((dev.get("caps") or {}).values()):
                return dev.get("name")
    except Exception:
        pass
    try:
        for dev in catalog.load().get("devices") or []:
            if dp.startswith(f"{dev.get('id')}."):
                return dev.get("name")
    except Exception:
        pass
    return None


def local_devices():
    """EINE Geräteliste für den Resolver - alle Geräte gleich behandelt.

    Früher gab es zwei Stufen: erst die localmap (nur Geräte mit lokalem
    Ziel), dann der Alexa-Katalog. Das war eine Sonderregel nach Herkunft und
    hat Cloud-Geräte benachteiligt - gemessen am 2026-11-04:
      "monitor an" / "monitorlicht aus" / "mach den computer aus" /
      "garten steckdose an" fanden in der localmap nichts und fielen durch,
      obwohl sie im Katalog stehen.

    Jetzt ist es eine Liste: zuerst die localmap-Einträge (sie führen den
    genaueren Datenpunkt), danach alle Katalog-Geräte, deren Name noch fehlt.
    Ob ein Ziel unter zigbee., alexa2. oder sonst wo liegt, spielt keine Rolle.
    """
    out = []
    namen = set()
    for dev in localmap.load()["devices"]:
        writable = sorted({_CAP_TO_RESOLVER[c] for c in dev["caps"] if c in _CAP_TO_RESOLVER})
        if not writable:
            continue
        name = dev["name"]
        if not name or name.casefold() in namen:
            continue
        namen.add(name.casefold())
        out.append({"id": name, "name": name, "writable": writable,
                    "room": dev.get("room")})
    for dev in catalog.load().get("devices") or []:
        name = dev.get("name") or ""
        if not name or name.casefold() in namen:
            continue
        writable = [c for c in (dev.get("writable") or []) if c]
        if not writable:
            continue
        namen.add(name.casefold())
        out.append({"id": dev.get("id"), "name": name, "writable": writable,
                    "room": dev.get("room")})
    return out


_VOCAB = None


def temperature_devices():
    """Alle Geräte mit einem Temperaturmesswert (auch reine Sensoren).

    Absichtlich NICHT local_devices(): dort stehen nur schaltbare Geräte,
    weil der Resolver damit Befehle auflöst. Eine Frage braucht aber genau
    die Messgeräte (Temp Wohnzimmer, Pool, Poolheizung ...).
    """
    out = []
    gesehen = set()
    for dev in localmap.load().get("devices") or []:
        caps = dev.get("caps") or {}
        name = (dev.get("name") or "").strip()
        if not name:
            continue
        key = localmap.normalize(name)
        if key in gesehen:
            continue
        datapoint = caps.get("temperature")
        einheit = (dev.get("units") or {}).get("temperature") or "°C"
        if not datapoint:            # Vom Eigentümer vorgegebenes Ziel (Beispiel Pool: das Alias zeigt
            # auf einen gelöschten Datenpunkt, der echte Wert liegt unter
            # Meine_Geräte.0.Pool). Ohne diese Prüfung fehlte der Raumfühler
            # und die Frage "im Pool" landete bei der Poolheizung.
            vorgabe = overrides.target_for(name, cap="temperature")
            if not vorgabe:
                continue
            datapoint = vorgabe["datapoint"]
            einheit = "°C"
        gesehen.add(key)
        out.append({
            "name": name,
            "id": name,
            "key": key,
            "datapoint": datapoint,
            "unit": einheit,
            "room": dev.get("room"),
            # Rein messend = kein Schalter daneben. Solche Geräte sind die
            # Raumfühler und gewinnen gegen Geräte mit Zusatzfunktionen.
            "pure": all(c in localmap.READ_CAPS for c in caps),
            "writable": [c for c in caps if c not in localmap.READ_CAPS],
            # Thermostat: hat zusaetzlich einen Sollwert.
            "setpoint": caps.get("setpoint"),
        })
    return out


def _grad_text(wert, einheit: str) -> str:
    """Messwert deutsch mit Einheit: 16.69 °C -> "16,7 Grad"."""
    try:
        zahl = float(wert)
    except (TypeError, ValueError):
        return f"{wert} {einheit}".strip()
    text = f"{zahl:.1f}".replace(".", ",")
    if text.endswith(",0"):
        text = text[:-2]
    einheit = str(einheit or "")
    if einheit.startswith("°"):
        einheit = "Grad"
    return f"{text} {einheit}".strip()


# Kurzformen, die im Haus gesprochen werden, aber nicht so heißen wie der
# Raum in ioBroker. Belegt am 2026-11-07: "Wie ist die Temperatur im Bad?"
# fand keinen Raum, weil das Gerät in "Badezimmer" liegt.
ROOM_ALIASES = {"bad": "badezimmer"}

# Ab wann ein Messwert als alt gilt und der Zeitpunkt mitgesprochen wird.
# 6 Stunden: ein Raumfuehler meldet im Normalbetrieb alle paar Minuten.
STALE_HOURS = 6.0
# Ab einer Woche ist es kein "meldet" mehr, sondern ein letzter Stand.
STALE_DAYS = 7

MONATE = ("Januar", "Februar", "März", "April", "Mai", "Juni", "Juli",
          "August", "September", "Oktober", "November", "Dezember")

# Ortsangabe im Satz. Deutsch verlangt hier den richtigen Artikel - "Im Küche"
# ist falsch (gesehen 2026-11-07). Bewusst eine feste Tabelle statt einer
# Genussschätzung: die Räume dieses Hauses sind bekannt.
ROOM_PHRASE = {
    "terrasse": "Auf der", "terasse": "Auf der", "balkon": "Auf dem",
    "kueche": "In der", "garage": "In der", "diele": "In der",
    "werkstatt": "In der", "sauna": "In der", "waschkueche": "In der",
}

# Dativformen für "Für ...". "Für Im Arbeitszimmer" ist falsch (gesehen
# 2026-11-07).
ROOM_PHRASE_DATIV = {
    "terrasse": "Für die", "terasse": "Für die", "balkon": "Für den",
    "kueche": "Für die", "garage": "Für die", "diele": "Für die",
    "werkstatt": "Für die", "sauna": "Für die", "waschkueche": "Für die",
    "garten": "Für den", "pool": "Für den",
}


def _ortsangabe(raum: str) -> str:
    """"Im Wohnzimmer" / "In der Küche" / "Auf der Terrasse"."""
    key = localmap.normalize(raum)
    return f"{ROOM_PHRASE.get(key, 'Im')} {raum}"


def _ortsangabe_dativ(raum: str) -> str:
    """Dativform fuer "Für ...": "Für das Wohnzimmer", "Für die Küche"."""
    key = localmap.normalize(raum)
    return f"{ROOM_PHRASE_DATIV.get(key, 'Für das')} {raum}".replace("Für Für", "Für")


def _raum_treffer(orte, geraete):
    """Raum aus den Ortswoertern - laengster Treffer gewinnt.

    Räume kommen aus der Raumzuordnung der Messgeräte. Die Anzeigeform kommt
    von dort mit, damit die Antwort "Im Wohnzimmer" heißt und nicht
    "Im wohnzimmer" (die Raumliste des Resolvers ist kleingeschrieben).
    """
    if not orte:
        return None
    orte_set = set(orte)
    treffer = []
    for dev in geraete:
        raum = (dev.get("room") or "").strip()
        if not raum:
            continue
        key = localmap.normalize(raum)
        if not key:
            continue
        namen = {key}
        namen.update(kurz for kurz, lang in ROOM_ALIASES.items() if lang == key)
        if namen & orte_set:
            treffer.append((len(key), raum))
    if not treffer:
        return None
    return max(treffer, key=lambda x: x[0])[1]


def temperature_query(text, assistant=None):
    """Feste Frageform lokal beantworten: "Wie ist die Temperatur <Ort>?".

    Gibt None zurück, wenn der Satz keine solche Frage ist - dann läuft
    alles wie bisher. Sonst ein Ergebnis wie bei handle(), zusätzlich mit
    "query": True und "answer" (der Sprechtext).

    Es wird nie geraten: gibt es zum Ort mehrere gleichwertige Messgeräte,
    kommt ok=False mit den Kandidaten zurück und der Agent entscheidet.
    """
    orte = resolver.parse_temperature_query(text)
    if orte is None:
        return None

    geraete = temperature_devices()
    if not geraete:
        return {"ok": False, "query": True, "reason": "kein-messgeraet",
                "utterance": text}
    if not orte:
        # "Wie ist die Temperatur?" allein - bewusst nicht beantwortet.
        return {"ok": False, "query": True, "reason": "ort-fehlt",
                "utterance": text}

    # 1) Raum? Dann die Messgeräte dieses Raums.
    raum = _raum_treffer(orte, geraete)
    if raum:
        key = localmap.normalize(raum)
        kandidaten = [d for d in geraete
                      if localmap.normalize(d.get("room") or "") == key]
        if kandidaten:
            # Gerät, das genauso heißt wie der Raum ("Pool"), ist gemeint.
            gleich = [d for d in kandidaten if d["key"] == key]
            if len(gleich) == 1:
                kandidaten = gleich
            else:
                reine = [d for d in kandidaten if d["pure"]] or kandidaten
                kandidaten = reine
            if len(kandidaten) > 1:
                return {"ok": False, "query": True, "reason": "mehrdeutig",
                        "utterance": text,
                        "candidates": [d["name"] for d in kandidaten]}
            return _temperatur_antwort(text, kandidaten[0], raum)

    # 2) Gerätename im Satz? Exakt zuerst, sonst unscharf - hier wird nur
    #    gelesen, nie geschaltet, deshalb ist ein ähnlicher Name unkritisch.
    #    Nötig für Schreibweisen wie "Terasse" (so heißt der Fühler in
    #    ioBroker) gegen "Terrasse" (so wird es gesprochen).
    matches = resolver.match_devices(" ".join(orte), geraete)
    matches = [m for m in matches
               if m.reason in ("exakt", "woerter", "kompositum")
               or m.reason.startswith("aehnlich")]
    if matches:
        best = matches[0]
        rivals = [m for m in matches[1:]
                  if best.score - m.score < resolver.AMBIGUITY_MARGIN]
        if rivals:
            return {"ok": False, "query": True, "reason": "mehrdeutig",
                    "utterance": text,
                    "candidates": [best.name] + [m.name for m in rivals[:4]]}
        dev = next(d for d in geraete if d["name"] == best.name)
        return _temperatur_antwort(text, dev, _passender_raum(dev, raum))

    # 2b) Teilname: "temperatur von klimaanlage" nennt nur ein Wort, das
    #     Gerät heißt aber "Temperatur Klimaanlage". Es wird nur gelesen,
    #     also ist ein Teilname unkritisch - trotzdem muss er EINDEUTIG sein.
    #     Bei mehreren Treffern gewinnt ein Thermostat (es hat Ist- und
    #     Sollwert), sonst wird nicht geantwortet.
    woerter = set(orte)
    teil = [d for d in geraete
            if woerter and woerter <= set(resolver.tokens(d["name"]))]
    if len(teil) > 1:
        thermo = [d for d in teil if d.get("setpoint")]
        if len(thermo) == 1:
            teil = thermo
    if len(teil) == 1:
        return _temperatur_antwort(text, teil[0], _passender_raum(teil[0], raum))

    # 3) Bekannter Raum, aber kein Fühler darin: ehrlich und lokal antworten.
    #    Sonst liefe die Frage wieder 8 s über den Agenten, nur um zu hören,
    #    dass es dort keinen Sensor gibt.
    bekannter_raum = next((r for r in resolver.ROOMS
                           if r in set(orte)), None)
    if bekannter_raum:
        anzeige = bekannter_raum.capitalize()
        return {"ok": True, "query": True, "utterance": text,
                "answer": f"{_ortsangabe(anzeige)} habe ich keinen "
                          f"Temperaturwert.",
                "room": anzeige, "value": None, "stale": True}

    return {"ok": False, "query": True, "reason": "ort-unbekannt",
            "utterance": text, "candidates": [d["name"] for d in geraete][:8]}


def setpoint_result(text, dry_run=False):
    """Sollwert setzen - aber nur bei vollstaendigem Thermostat.

    Gibt None zurueck, wenn der Satz kein Sollwert-Befehl ist (dann laeuft
    alles wie bisher). Sonst ein Ergebnis mit Sprechtext, auch bei Ablehnung:
    "Im Wohnzimmer habe ich keinen Thermostat." ist eine Antwort, kein Fehler.

    Die harte Sperre sitzt in execute() (_thermostat_sperre): Ist- und
    Sollwert-Datenpunkt muessen beide existieren.

    Wichtig: Der Sollwert wird hier auch WIRKLICH geschrieben. Frueher lief
    dieser Weg nur als Dry-Run und antwortete "gestellt", ohne den
    schreibbaren Datenpunkt anzufassen (gefunden 13.11.2026 an der
    Klimaanlage). Bei dry_run bleibt es beim reinen Aufloesen, und die Antwort
    sagt das mit "wuerde".
    """
    intent = resolver.parse_action(text)
    if intent.action != "temperature" or intent.value is None:
        return None
    devices = local_devices()
    resolution = resolver.resolve(text, devices)
    if not resolution.ok:
        if resolution.reason == "kein-thermostat":
            raum = resolver.room_in(text)
            ort = _ortsangabe(raum.capitalize()) if raum else None
            antwort = (f"{ort} habe ich keinen Thermostat." if ort
                       else "Dafür habe ich keinen Thermostat.")
            return {"ok": False, "reason": "kein-thermostat",
                    "answer": antwort, "utterance": text}
        return None
    if resolution.action != "temperature":
        return None
    result, _ = execute(resolution, dry_run=dry_run)
    result["utterance"] = text
    wert = resolution.value
    try:
        zahl = f"{float(wert):g}"
    except (TypeError, ValueError):
        zahl = str(wert)
    if not result.get("ok"):
        # Zum Beispiel thermostat-unvollstaendig oder Schreibfehler:
        # den Grund sprechen, keinen Erfolg behaupten.
        result["answer"] = (result.get("detail")
                            or f"{resolution.device} konnte nicht auf "
                               f"{zahl} Grad gestellt werden.")
        return result
    if dry_run:
        result["answer"] = f"{resolution.device} würde auf {zahl} Grad gestellt."
    else:
        result["answer"] = f"{resolution.device} auf {zahl} Grad gestellt."
    return result


def _passender_raum(dev, raum):
    """Raum für die Antwort: der gefragte, sonst der des Geräts."""
    eigener = (dev.get("room") or "").strip()
    if raum and eigener and localmap.normalize(raum) == localmap.normalize(eigener):
        return raum
    if raum:
        return raum
    return eigener or None


def _temperatur_antwort(text, dev, raum):
    """Messwert lesen und den Sprechtext bauen.

    Ist der Wert alt, wird das GESAGT. Belegt 2026-11-07: der Istwert der
    Klimaanlage steht seit dem 23.05. auf 8 Grad, waehrend der Raumfuehler
    18,8 Grad meldet. Ein nacktes "Im Arbeitszimmer sind es 8 Grad" waere
    eine falsche Behauptung ueber die Gegenwart.
    """
    state = _get_state(dev["datapoint"])
    wert = None if state is None else state.get("val")
    ts = None if state is None else state.get("ts")
    if wert is None:
        # Kein Wert ist eine belegbare Aussage, keine Unbekannte: lieber kurz
        # und ehrlich antworten als den Agenten 8 s rechnen lassen.
        ort = _ortsangabe(raum) if raum else dev["name"]
        return {"ok": True, "query": True, "utterance": text,
                "answer": f"Für {ort} liegt kein Messwert vor.",
                "device": dev["name"], "room": raum,
                "datapoint": dev["datapoint"], "value": None,
                "unit": dev.get("unit"), "stale": True}
    grad = _grad_text(wert, dev.get("unit"))
    alter = _alter_stunden(ts)
    if alter is not None and alter > STALE_HOURS:
        # Alter Wert: Zeitpunkt mit nennen. Bei sehr alten Werten (ueber eine
        # Woche) ist es kein "meldet" mehr, sondern ein letzter Stand.
        if alter > STALE_DAYS * 24:
            ort = _ortsangabe_dativ(raum) if raum else f"{dev['name']}"
            antwort = (f"{ort} liegt kein aktueller Wert vor. "
                       f"Der letzte war {grad} {_alter_text(alter)}.")
        else:
            antwort = (f"{dev['name']} meldet {grad}, "
                       f"letzter Wert {_alter_text(alter)}.")
        return {"ok": True, "query": True, "utterance": text,
                "answer": antwort, "device": dev["name"], "room": raum,
                "datapoint": dev["datapoint"], "value": wert,
                "unit": dev.get("unit"), "stale": True,
                "ageHours": round(alter, 1)}
    if raum:
        answer = f"{_ortsangabe(raum)} sind es {grad}."
    else:
        answer = f"{dev['name']}: {grad}."
    return {"ok": True, "query": True, "utterance": text,
            "answer": answer, "device": dev["name"], "room": raum,
            "datapoint": dev["datapoint"], "value": wert,
            "unit": dev.get("unit")}


def _alter_stunden(ts):
    """Alter eines Zeitstempels in Stunden (None, wenn unbekannt)."""
    try:
        millis = float(ts)
    except (TypeError, ValueError):
        return None
    if millis <= 0:
        return None
    return max(0.0, (time.time() * 1000 - millis) / 3600000.0)


def _alter_text(stunden: float) -> str:
    """Alter in Worten: "vor 12 Minuten", "vom 23. Mai".

    Monat als Wort, nicht als Zahl: "vom 23.05." liest die Sprachausgabe als
    "dreiundzwanzigter fünfter" vor (gesehen 2026-11-07).
    """
    if stunden < 1:
        return f"vor {max(1, int(stunden * 60))} Minuten"
    if stunden < 24:
        return f"vor {int(stunden)} Stunden"
    ts = time.time() - stunden * 3600
    lokal = time.localtime(ts)
    monat = MONATE[lokal.tm_mon - 1]
    if lokal.tm_year != time.localtime().tm_year:
        return f"vom {lokal.tm_mday}. {monat} {lokal.tm_year}"
    return f"vom {lokal.tm_mday}. {monat}"


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


def group_batch(text, assistant=None):
    """Befehl ohne Gerät und ohne Raum: die Gruppe des Sprachgeräts schalten.

    Wozu: Auf dem Gerät im Wohnzimmer soll "Licht an" genügen. Der Gruppenname
    hat dabei KEINE Bedeutung fürs Schalten - entscheidend ist, WELCHES
    Sprachgerät das Wachwort gehört hat (Device-ID aus room-map.json).

    Greift nur, wenn im Satz weder ein Gerätename noch ein Raum steht:
      "Licht an"          -> Gruppe
      "Licht Arbeitszimmer an" -> normales Gerät (früher abgefangen)
      "Wohnzimmer an"     -> room_batch (früher abgefangen)

    Rueckgabe wie room_batch oder None.
    """
    if not assistant:
        return None

    gruppe = groups.fuer_assistent(assistant)
    if not gruppe:
        return None

    # Gerätename im Satz? Dann ist das Gerät gemeint, nicht die Gruppe.
    # Zwei Quellen prüfen, weil beide unvollständig sind: der eigene Resolver
    # kennt nur die localmap (43 Geräte), HassIL auch den Katalog (118).
    # Belegt 28.10.2026: "Monitorlicht an" landete in der Gruppe, weil
    # local_devices() den Namen nicht führt.
    try:
        devices = local_devices()
        treffer = resolver.match_devices(text, devices) if devices else []
        # Auch die STT-korrigierte Fassung prüfen: die Spracherkennung verhört
        # Gerätenamen regelmäßig (belegt 2026-11-04: "Beckenstrahler, warmes,
        # weiß" -> korrigiert "Deckenstrahler warmes, weiß"). Ohne diesen
        # zweiten Blick galt der Satz als namenlos und schaltete die Gruppe des
        # Sprachgeräts - also das falsche Zimmer.
        if not treffer:
            try:
                fest, _aenderungen = fuzzy.correct_sentence(text, vocab())
                if fest != text:
                    treffer = resolver.match_devices(fest, devices) if devices else []
            except Exception:
                pass
        if treffer:
            # IRGENDEIN erkannter Geraetename heisst: es ist KEIN Gruppenbefehl.
            # Die Gruppe gilt nur fuer Saetze ohne jeden Geraetenamen -
            # ausdruecklich so gewuenscht (Eigentuemer, 2026-11-04):
            #   "Licht an", "Licht aus", "Licht 50 %"  -> Gruppe
            #   alles mit Geraetenamen                  -> Geraet
            # Auch ein nur schwach erkannter oder verhoerter Name zaehlt:
            # sonst schaltet der Befehl die Gruppe des Sprachgeraets, also ein
            # voellig anderes Zimmer. Belegt 2026-11-04: "Deckenstrahler warmes
            # Weiss" (68 Punkte) schaltete "Arbeitszimmer", obwohl der
            # Deckenstrahler im Wohnzimmer haengt. Passt der Name auf kein
            # Geraet, ist "nicht verstanden" die richtige Antwort - nicht das
            # falsche Zimmer.
            # Reine Gruppensaetze erzeugen nachweislich keinen Treffer
            # (geprueft 2026-11-04 ueber "Licht an", "Licht 50 %", "alles an",
            # "hier heller", "licht warmweiss", "Licht auf maximum").
            return None
    except Exception:
        pass
    try:
        erkannt_name = hassil_router.parse(text) or {}
        if erkannt_name.get("name") or erkannt_name.get("area"):
            return None
    except (ImportError, RuntimeError):
        # hassil fehlt oder seine Vorlagen sind unbrauchbar - dann entscheidet
        # spaeter der eigene Weg. Ein Namensfehler (fehlender Import) faellt
        # dagegen bewusst auf, statt still zu wirken (belegt 28.10.2026).
        pass

    # Kein Raum im Satz? "Wohnzimmer an" gehört room_batch.
    if resolver.room_in(text):
        return None

    ziele = groups.geraete_der_gruppe(gruppe)
    if not ziele:
        return None

    # Aktion bestimmen: an/aus, Prozent, Farbe, Farbtemperatur.
    aufgaben = []          # (datapoint, value)
    intent = resolver.parse_action(text)
    if intent.action in ("on", "off"):
        wert = intent.action == "on"
        for _name, caps in ziele:
            dp = caps.get("onoff")
            if dp:
                aufgaben.append((dp, wert))
    else:
        # Farbe, Helligkeit, Farbtemperatur: das Erkannte direkt verwenden.
        # NICHT über als_schaltbefehl() gehen - das braucht einen Geräte- oder
        # Raumnamen, um ein Ziel zu finden, und liefert ohne beides None.
        # Belegt 28.10.2026: "Licht auf 50 prozent" scheiterte in der Gruppe
        # genau daran.
        try:
            erkannt = hassil_router.parse(text) or {}
        except (ImportError, RuntimeError):
            erkannt = {}

        # Der Wert wird PRO GERAET gebildet, nicht einmal fuer die ganze
        # Gruppe: Farbziele unterscheiden sich im Format. hex_color
        # (level.color.rgb) will "#rrggbb", group_1.color (level.color) will
        # {"r":..,"g":..,"b":..}. Ein gemeinsamer Wert waere fuer eines der
        # beiden falsch - der Adapter nimmt ihn an, die Lampe reagiert nicht
        # (belegt 28.10.2026).
        if "brightness" in erkannt:
            try:
                hell = float(int(round(float(erkannt["brightness"]))))
            except (TypeError, ValueError):
                return None
            hell = max(0.0, min(100.0, hell))
            for _name, caps in ziele:
                dp = caps.get("brightness")
                if dp:
                    aufgaben.append((dp, hell))

        elif hassil_router.weiss_als_temperatur(erkannt):
            # "weiss", "warmes weiss": gehoert auf colortemp, nicht auf die
            # Farbwahl. Kelvin DIREKT - die Datenpunkte fuehren Kelvin
            # (belegt 28.10.2026 an den vorbestehenden Werten 2200 und 7000).
            kelvin = hassil_router.weiss_als_temperatur(erkannt)
            for _name, caps in ziele:
                dp = caps.get("colortemp")
                if dp:
                    aufgaben.append((dp, int(kelvin)))

        elif "color" in erkannt:
            rgb = hassil_router.COLORS.get(str(erkannt["color"]).lower())
            if not rgb:
                return None
            for _name, caps in ziele:
                dp = caps.get("color")
                if dp:
                    aufgaben.append((dp, hassil_router.farbe_als_wert(dp, rgb)))

        elif "temperature" in erkannt:
            try:
                kelvin = float(erkannt["temperature"])
            except (TypeError, ValueError):
                return None
            if not 1000 <= kelvin <= 10000:
                return None
            for _name, caps in ziele:
                dp = caps.get("colortemp")
                if dp:
                    aufgaben.append((dp, int(kelvin)))
        else:
            return None

    if not aufgaben:
        return None

    return {"group_name": gruppe["name"], "assignments": aufgaben,
            "device": gruppe["name"], "room": None,
            "action": intent.action or "set",
            "targets": len(aufgaben)}


def handle(text, dry_run=False, assistant=None):
    """Satz auswerten und (ausser bei dry_run) schalten.

    `assistant` ist die Device-ID des Sprachgeräts, das das Wachwort gehört
    hat (aus OPENCLAW_VOICE_DEVICE_ID). Nur damit lässt sich eine Gruppe
    zuordnen - der Gruppenname selbst hat keine Bedeutung fürs Schalten.

    Es gibt keinen Schalter „lokal bevorzugen" mehr: jedes Gerät nimmt
    denselben Weg. Raum- und Gruppenbefehle gelten immer.
    """
    # Temperaturfrage zuerst: "Wie ist die Temperatur im Wohnzimmer?".
    # Das ist KEIN Schaltbefehl, sondern ein Messwert. Vorher landete der Satz
    # bei parse_action() als "frage" und lief über den Agenten (7-9 s, gemessen
    # 2026-11-07). Hier wird derselbe Datenpunkt gelesen, den das Panel zeigt.
    frage = temperature_query(text, assistant)
    if frage is not None:
        return frage

    # Danach der Sollwert: "stell die klimaanlage auf 22 grad". Braucht eine
    # eigene Antwort, weil "geschaltet" dafür das falsche Wort ist.
    antwort = setpoint_result(text, dry_run=dry_run)
    if antwort is not None:
        return antwort

    # Gruppe zuerst: sie greift nur, wenn weder Gerät noch Raum im Satz
    # steht. "Licht an" auf dem Wohnzimmer-Gerät schaltet dessen Gruppe.
    gbatch = group_batch(text, assistant)
    if gbatch:
        result = {"ok": True, "utterance": text,
                  "room": None, "device": gbatch["device"],
                  "group_name": gbatch["group_name"],
                  "action": gbatch["action"],
                  "targets": gbatch["targets"]}
        if dry_run:
            result["datapoints"] = [dp for dp, _ in gbatch["assignments"]]
            result["values"] = [v for _, v in gbatch["assignments"]]
            return result
        written, failed = [], []
        for dp, wert in gbatch["assignments"]:
            ok, status, elapsed, detail = write_state(dp, wert)
            eintrag = {"datapoint": dp, "value": wert, "http": status}
            (written if ok else failed).append(eintrag)
        result["written"] = written
        result["failed"] = failed
        result["ok"] = bool(written) and not failed
        return result
    batch = room_batch(text)
    if batch:
        result = {"ok": True, "utterance": text,
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
    # EINE Geräteliste für alle, EIN Auflöseweg. Früher lief hier zuerst die
    # localmap und nur bei Erfolg der Katalog - eine Sonderregel nach Herkunft,
    # die Cloud-Geräte ohne lokales Gegenstück durchfallen liess (gemessen
    # 2026-11-04: "monitor an", "monitorlicht aus", "mach den computer aus",
    # "garten steckdose an").
    devices = local_devices()
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
            # Die Korrektur IMMER vermerken, auch wenn der eigene Resolver
            # damit noch nichts anfangen kann. HassIL und die Zielsuche
            # arbeiten spaeter damit - sonst geht der reparierte Name
            # verloren. Belegt 2026-11-04: "Steckenstrahlarm warmes weiss"
            # wurde zu "Deckenstrahler ..." korrigiert, der Resolver fand
            # aber keine Aktion (Weisston kennt er nicht) - und HassIL bekam
            # wieder den verhoerten Satz.
            result["corrected_utterance"] = fixed
            retry = resolver.resolve(fixed, devices)
            if retry.ok:
                # Wichtig: resolution ersetzen, damit der korrigierte Treffer
                # auch geschrieben wird, nicht der Fehlschlag.
                resolution = retry
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
    # HassIL ist die ERSTE Stufe (Wunsch des Eigentuemers, 27.10.2026): es
    # wird IMMER gefragt, nicht nur wenn der eigene Resolver scheitert.
    # Findet HassIL ein Ziel, gewinnt es. Findet es keines, bleibt das
    # Ergebnis des eigenen Resolvers stehen - der eigene Weg ist damit der
    # Rueckfall, nicht mehr die erste Wahl.
    #
    # Gemessen an den 34 Testfaellen (27.10.2026): 16 entscheidet jetzt
    # HassIL, 18 der eigene Resolver; das Ergebnis ist in allen 34 Faellen
    # identisch zum Stand davor (gleicher Datenpunkt, gleicher Wert,
    # gleiche Ablehnungen). Die 7 Faelle, die HassIL nicht kennt ("mach die
    # jalousie runter", "schalte alle lampen an", "starte den kino pc" u.a.),
    # behaelt der eigene Resolver - ohne ihn waeren sie verloren.
    #
    # room_batch und dieser Block vertragen sich: room_batch greift nur bei
    # "<Raum> an/aus" ohne Geraetenamen und schaltet dann mehrere Lampen.
    try:
        import hassil_router
        # Korrigierte Fassung bevorzugen: HassIL kennt die STT-Verhoerer nicht,
        # also muss der reparierte Satz hinein. Belegt 2026-11-04:
        # "Steckenstrahlarm warmes weiss" ergab im Original keine Zuordnung,
        # mit "Deckenstrahler" dagegen den richtigen Datenpunkt.
        _text_fuer_hassil = result.get("corrected_utterance") or text
        erkannt = hassil_router.parse(_text_fuer_hassil)
        befehl = hassil_router.als_schaltbefehl(erkannt) if erkannt else None
    except Exception as exc:  # noqa: BLE001
        print(f"hassil nicht nutzbar: {exc!r}", flush=True)
        erkannt, befehl = None, None

    # Kein Wert bedeutet keinen Schaltbefehl; dann entscheidet der eigene Weg.
    if befehl and befehl[1] is not None:
        ziel, wert = befehl
        treffer = {
            "ok": True,
            "engine": "hassil",
            "intent": (erkannt or {}).get("intent"),
            # Der Geraetename kommt aus dem ZIEL, nicht aus dem Satz. HassIL
            # legt bei Farbe/Weisston den Rest als "floor" ab (belegt
            # 2026-11-04: "Deckenstrahler warmes Weiss" -> floor="Deckenstrahler
            # warmes"), wodurch der ganze Satz als Geraetename in der
            # Sprachantwort landete. Der Datenpunkt ist eindeutig.
            "device": name_zu_datenpunkt(ziel)
                      or (erkannt or {}).get("name")
                      or (erkannt or {}).get("area") or text,
            "room": (erkannt or {}).get("area"),
            "action": "set",
            "datapoint": ziel,
            "value": wert,
            "utterance": text,
        }
        if dry_run:
            return treffer
        ok, status, dauer, detail = write_state(ziel, wert)
        treffer.update({"ok": ok, "http": status,
                        "seconds": round(dauer, 3), "detail": detail[:200]})
        return treffer

    # Das Ziel steht bereits im Ergebnis: execute() hat Vorgabe, localmap und
    # Katalog in EINEM Weg aufgelöst. Hier wird nichts mehr nach Herkunft
    # umgebogen oder umbenannt.

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
    args = parser.parse_args()
    print(json.dumps(handle(args.utterance, dry_run=args.dry_run),
                     ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
