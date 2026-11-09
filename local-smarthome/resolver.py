"""Deutschen Smart-Home-Satz lokal aufloesen — ohne Cloud, ohne Alexa-NLU.

Eingabe: gesprochener Satz (STT-Text). Ausgabe: Geraet, Aktion, Wert — oder
eine ehrliche Ablehnung. Es wird nie geraten: passt kein Geraet sicher, kommt
`ok=False` mit Grund zurueck und der Aufrufer entscheidet weiter.

Belegte Entscheidungen (siehe test_resolver.py):
- Ein Geraet, dem die Faehigkeit fehlt, wird NICHT durch ein anderes ersetzt.
- "runter"/"heller" ist relativ: bei Prozent-Geraeten dimmen, sonst an/aus.
- Generische Namen ("Heizung") verlieren gegen spezifische ("Badezimmer
  Heizung"), weil unerklaerte Satzwoerter den Treffer abwerten.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher

# ---------------------------------------------------------------- Normalisierung

FILLERS = {
    "doch", "mal", "bitte", "eben", "halt", "kurz", "schnell", "jetzt", "gleich",
    "einmal", "noch", "ja", "also", "denn", "gerne", "gern", "bisschen", "wohl",
}

VERBS = {
    "schalte", "schalt", "schalten", "mach", "mache", "machen",
    "stelle", "stell", "stellen", "setze", "setz", "drehe", "dreh",
    "starte", "start", "starten", "aktiviere", "einschalten", "anschalten",
    "ausschalten", "abschalten", "anmachen", "ausmachen", "aufdrehen",
    "runterdrehen", "hochdrehen", "dimme", "dimmen", "dimm",
    "kannst", "koenntest", "wuerdest", "du", "mir", "uns", "hast", "hat",
    "habt", "wird", "will", "moechte", "moechtest",
}

ARTICLES = {"der", "die", "das", "den", "dem", "des", "ein", "eine", "einen",
            "einem", "einer"}

PREPOSITIONS = {"im", "am", "in", "an", "auf", "bei", "von", "zum", "zur",
                "unter", "ueber", "vor", "neben", "hinter"}

ON_WORDS = {"an", "ein", "einschalten", "anschalten", "anmachen", "einschalte",
            "aktiviere", "starte", "starten", "hochfahren", "los"}
OFF_WORDS = {"aus", "ab", "ausschalten", "abschalten", "ausmachen", "ausschalte",
             "beende", "beenden", "stoppe", "stopp", "stoppen", "runterfahren",
             "schliesse", "schliessen"}
TOGGLE_WORDS = {"umschalten", "umschalte", "toggle", "invertiere", "wechseln"}

# Relativ: heller/dunkler bzw. oeffnen/schliessen
# "auf" stand hier frueher und war falsch: "Schalte den Deckenstrahler auf
# warmes Weiss" wurde dadurch als "heller machen" gelesen und dimmte die Lampe
# auf 11 % statt den Weisston zu setzen (belegt 2026-11-04 an genau diesem Satz).
# "auf" ist eine Praeposition ("auf 30 prozent", "auf warmes weiss") und kein
# Steigerungswort. Absolute Werte werden vorher schon abgefangen.
UP_WORDS = {"hoch", "rauf", "heller", "oeffne", "oeffnen", "aufmachen",
            "heller machen"}
DOWN_WORDS = {"runter", "dunkler", "zu", "herunter", "niedriger", "dunkler machen"}

QUESTION_WORDS = {"ob", "warum", "wieso", "weshalb", "welche", "welcher"}
AUX_START = {"ist", "sind", "war", "waren", "steht", "stehen", "laeuft", "geht",
             "hast", "hat", "habt", "kann", "kannst", "koennte", "koenntest",
             "wuerde", "wuerdest", "wird", "wie", "welchen", "welches"}
IMPERATIVE_MARKERS = {"einschalten", "ausschalten", "anschalten", "abschalten",
                      "schalte", "schalt", "mach", "mache", "stelle", "stell",
                      "drehe", "dreh", "dimme", "starte", "aktiviere"}
NEGATION_WORDS = {"nicht", "kein", "keine", "keinen", "niemals", "nie"}

UNIT_WORDS = {"prozent", "grad", "celsius", "kelvin", "%"}

# Woerter, die nie Teil eines Geraetenamens-Abgleichs sind
SKIP_FOR_MATCH = (FILLERS | VERBS | ARTICLES | PREPOSITIONS | UNIT_WORDS
                  | ON_WORDS | OFF_WORDS | TOGGLE_WORDS | UP_WORDS | DOWN_WORDS
                  | QUESTION_WORDS | AUX_START | {"prozent", "grad"})


def normalize(text: str) -> str:
    if text is None:
        return ""
    s = unicodedata.normalize("NFC", str(text)).lower()
    s = s.replace("ß", "ss").replace("ä", "ae").replace("ö", "oe").replace("ü", "ue")
    s = re.sub(r"(\d)\s*%", r"\1 prozent", s)
    s = re.sub(r"[^\w\s%]", " ", s)
    return " ".join(s.split())


def tokens(text: str) -> list[str]:
    return normalize(text).split()


# ---------------------------------------------------------------- Abfrage
# Eine feste Frageform, bewusst eng: "Wie ist die Temperatur [im|von|vom|in|ohne
# Praeposition] <Ort>?". Der Eigentuemer hat sie am 2026-11-07 vorgegeben:
# genau dieser Satz, nicht "Wie ist die Temperatur?" allein - ohne Ort waere
# die Frage mehrdeutig und muesste weiter von der KI beantwortet werden.
#
# Warum ueberhaupt lokal: derselbe Satz landete vorher bei parse_action() als
# "frage" und ging an den Agenten. Gemessen am 2026-11-07:
#   11:37:01 nicht aufgeloest (frage) -> Antwort nach 7,3 s
#   12:25:14 nicht aufgeloest (frage) -> Antwort nach 8,9 s
# Lokal sind es Millisekunden, und die Antwort ist derselbe Datenpunkt, den
# auch das Panel zeigt.
TEMP_QUESTION_PREFIXES = (
    ("wie", "ist", "die", "temperatur"),
    ("wie", "ist", "temperatur"),
    ("wie", "hoch", "ist", "die", "temperatur"),
    ("wie", "hoch", "ist", "temperatur"),
    ("wie", "viel", "grad"),
    ("wie", "warm", "ist", "es"),
    ("wie", "kalt", "ist", "es"),
    ("wie", "warm", "ist"),
    ("wie", "kalt", "ist"),
    ("welche", "temperatur"),
    ("was", "ist", "die", "temperatur"),
    ("was", "fuer", "eine", "temperatur"),
    ("temperatur",),
)

# Fuellwoerter zwischen Frage und Ort. "mit ohne" heisst beim Eigentuemer:
# mit Praeposition ("im Wohnzimmer") ODER ohne ("wohnzimmer") - beides gilt.
LOCATION_FILLERS = {"im", "in", "am", "an", "vom", "von", "aus", "beim",
                    "bei", "auf", "zu", "der", "dem", "den", "die", "das",
                    "des", "mein", "meine", "meinem", "meinen", "unser",
                    "unsere", "hat", "haben", "ist", "sind", "es"}
# Anhaengsel, die den Ort nicht veraendern.
LOCATION_TAIL = FILLERS | {"gerade", "aktuell", "momentan", "zurzeit",
                           "draussen", "drin", "innen"}


def parse_temperature_query(text: str) -> list[str] | None:
    """Ort aus einer Temperaturfrage - oder None, wenn es keine ist.

    Rueckgabe: die Ortswoerter (Liste, kann leer sein, wenn keiner genannt
    wurde). None bedeutet: der Satz ist keine Temperaturfrage und geht den
    normalen Weg.
    """
    t = tokens(text)
    if not t:
        return None
    rest = None
    for prefix in TEMP_QUESTION_PREFIXES:
        if tuple(t[:len(prefix)]) == prefix:
            rest = t[len(prefix):]
            break
    if rest is None:
        return None
    # Abgrenzung gegen SOLLWERT-Befehle: "Temperatur Klimaanlage auf 22 Grad"
    # faengt zwar auch mit "temperatur" an, ist aber ein Schaltbefehl. Eine
    # Zahl oder ein Setz-Verb im Satz heisst: kein Messwert gefragt.
    if _number(text) is not None:
        return None
    # "auf" steht hier NICHT: es ist auch eine Ortsangabe ("auf der
    # Terrasse", belegt 2026-11-07). Sollwerte sind über die Zahl davor
    # schon abgefangen ("auf 22 Grad").
    if set(t) & {"stelle", "stell", "setze", "setz", "regle", "drehe",
                 "erhoehe", "senke", "prozent", "celsius", "kelvin"}:
        return None
    # Praeposition/Artikel vor dem Ort entfernen - beliebig oft, damit
    # "im schlafzimmer" und "von der poolheizung" beide funktionieren.
    while rest and rest[0] in LOCATION_FILLERS:
        rest = rest[1:]
    while rest and rest[-1] in LOCATION_TAIL:
        rest = rest[:-1]
    return rest


# ---------------------------------------------------------------- Aktion

@dataclass
class Intent:
    action: str | None = None          # on | off | toggle | percent | temperature
    value: float | None = None
    relative: int | None = None        # +10 / -10 bei "heller"/"dunkler"
    reason: str = ""


# Gesprochene Zahlen. Schluessel in der Form, die normalize() erzeugt:
# Umlaute sind dort bereits ersetzt (fünf -> fuenf, dreißig -> dreissig).
# Belegt 27.10.2026 23:59: "Lichtarbeitszimmer fünf Prozent" scheiterte mit
# "wert-fehlt", weil _number() nur Ziffern kannte. Die KI brauchte 8 s.
_EINER = {
    "null": 0, "eins": 1, "ein": 1, "zwei": 2, "drei": 3, "vier": 4,
    "fuenf": 5, "sechs": 6, "sieben": 7, "acht": 8, "neun": 9,
    "zehn": 10, "elf": 11, "zwoelf": 12, "dreizehn": 13, "vierzehn": 14,
    "fuenfzehn": 15, "sechzehn": 16, "siebzehn": 17, "achtzehn": 18,
    "neunzehn": 19,
}
_ZEHNER = {
    "zwanzig": 20, "dreissig": 30, "vierzig": 40, "fuenfzig": 50,
    "sechzig": 60, "siebzig": 70, "achtzig": 80, "neunzig": 90,
}


def _zahlwort(token: str) -> int | None:
    """Eine gesprochene Zahl in einen Wert uebersetzen.

    Deckt 0-99 ab, auch zusammengesetzt ("einundzwanzig") und "hundert".
    Gibt None zurueck, wenn das Wort keine Zahl ist.
    """
    if not token:
        return None
    if token in _EINER:
        return _EINER[token]
    if token in _ZEHNER:
        return _ZEHNER[token]
    if token == "hundert":
        return 100
    # "einundzwanzig": Einer + "und" + Zehner
    m = re.fullmatch(r"([a-z]+)und([a-z]+)", token)
    if m:
        einer, zehner = m.group(1), m.group(2)
        if einer in _EINER and zehner in _ZEHNER and 0 < _EINER[einer] < 10:
            return _ZEHNER[zehner] + _EINER[einer]
    return None


def _number(text: str) -> int | None:
    """Erste Zahl im Satz: Ziffer hat Vorrang vor dem Zahlwort."""
    norm = normalize(text)
    m = re.search(r"\b(\d{1,3})\b", norm)
    if m:
        return int(m.group(1))
    for token in norm.split():
        wert = _zahlwort(token)
        if wert is not None:
            return wert
    return None


def parse_action(text: str) -> Intent:
    t = tokens(text)
    words = set(t)
    if not t:
        return Intent(reason="leer")

    if words & QUESTION_WORDS:
        return Intent(reason="frage")
    if t[0] in AUX_START and t[-1] in {"an", "aus", "ein", "off", "on"} \
            and not (words & IMPERATIVE_MARKERS):
        return Intent(reason="frage")
    if t[0] in AUX_START and not (words & IMPERATIVE_MARKERS) \
            and not (words & (ON_WORDS | OFF_WORDS | TOGGLE_WORDS)):
        return Intent(reason="frage")
    if words & NEGATION_WORDS:
        return Intent(reason="verneint")
    if words & {"und", "oder", "dann", "danach", "vorher", "gleichzeitig",
                "bevor", "nachdem", "wenn", "falls"}:
        return Intent(reason="mehrere-aktionen")

    # absolute Werte zuerst
    if "prozent" in words:
        num = _number(text)
        if num is not None:
            return Intent("percent", float(max(0, min(100, num))))
        return Intent(reason="wert-fehlt")
    if words & {"grad", "celsius", "kelvin"}:
        num = _number(text)
        if num is not None:
            return Intent("temperature", float(num))
        return Intent(reason="wert-fehlt")

    if words & TOGGLE_WORDS:
        return Intent("toggle")

    on_hit, off_hit = bool(words & ON_WORDS), bool(words & OFF_WORDS)
    if on_hit and off_hit:
        return Intent(reason="widerspruch")
    if on_hit:
        return Intent("on")
    if off_hit:
        return Intent("off")

    up_hit, down_hit = bool(words & UP_WORDS), bool(words & DOWN_WORDS)
    if down_hit and not up_hit:
        return Intent("relative", relative=-10)
    if up_hit and not down_hit:
        return Intent("relative", relative=+10)

    return Intent(reason="keine-aktion")


# ---------------------------------------------------------------- Geraet

@dataclass
class Match:
    name: str
    id: str
    score: float
    reason: str
    writable: list[str] = field(default_factory=list)
    manufacturer: str | None = None


def _content_tokens(text: str) -> list[str]:
    return [w for w in tokens(text) if w not in SKIP_FOR_MATCH and not w.isdigit()]


def _score_name(dev_tokens: list[str], utt_tokens: list[str]) -> tuple[float, str, int]:
    """(Basiswert, Grund, Anzahl im Satz nicht erklaerter Woerter)."""
    if not dev_tokens:
        return 0.0, "leer", 0

    dev_phrase = " ".join(dev_tokens)
    utt_phrase = " ".join(utt_tokens)
    utt_set = set(utt_tokens)

    # 1) Name zusammenhaengend im Satz
    if re.search(r"(?<!\w)" + re.escape(dev_phrase) + r"(?!\w)", utt_phrase):
        return 100.0 + 8.0 * len(dev_tokens), "exakt", 0

    # 2) alle Namenswoerter vorhanden, andere Reihenfolge
    if set(dev_tokens).issubset(utt_set):
        return 90.0 + 8.0 * len(dev_tokens), "woerter", 0

    # 2b) Getrennt gesprochen, zusammen geschrieben: "kino pc" -> "KinoPC",
    #     "monitor licht" -> "Monitorlicht". Nur exakte Gleichheit zaehlt,
    #     damit "kino regenbogen" nicht "KinoPC" trifft.
    dev_joined = "".join(dev_tokens)
    if len(dev_joined) >= 5:
        # Getrennt gesprochen, zusammen geschrieben: "kino pc" -> "KinoPC".
        for size in (2, 3):
            if size > len(utt_tokens):
                continue
            for start in range(len(utt_tokens) - size + 1):
                if "".join(utt_tokens[start:start + size]) == dev_joined:
                    return 96.0 + 4.0 * len(dev_tokens), "zusammengesetzt", 0
        # Umgekehrte Richtung: zusammen gehoert, getrennt geschrieben.
        # Der Zerleger hoert "Licht Arbeitszimmer" als EIN Wort
        # ("Lichtarbeitszimmer"). Die Fenster oben greifen dann nicht, weil
        # sie mindestens zwei Satzwoerter brauchen - die Aeusserung hat aber
        # nur eins. Belegt 27.10.2026 22:38: der lokale Schnellweg fiel aus,
        # die KI brauchte 9 s statt 20 ms.
        #
        # Nur exakte Gleichheit zaehlt, damit "Lichtschalter" nicht
        # "Licht Schalter" trifft. Zusaetzlich muss der Name mindestens zwei
        # Teile haben, sonst waere es kein Zusammensetzen.
        if len(dev_tokens) >= 2 and dev_joined in utt_tokens:
            return 96.0 + 4.0 * len(dev_tokens), "zusammengesetzt", 0

    # 3) Kompositum: "monitorlich" -> "Monitorlicht". Streng, damit
    #    "alles" nicht "Alle Lampen" trifft.
    best_comp = 0.0
    for dev_word in dev_tokens:
        if len(dev_word) < 5:
            continue
        for utt_word in utt_tokens:
            if len(utt_word) < 5 or utt_word == dev_word:
                continue
            if utt_word.startswith(dev_word) or dev_word.startswith(utt_word):
                shorter, longer = sorted((dev_word, utt_word), key=len)
                share = len(shorter) / len(longer)
                if share >= 0.70:
                    best_comp = max(best_comp, 70.0 + 20.0 * share)
    if best_comp:
        return best_comp, "kompositum", 0

    # 4) Unscharf ueber gleich grosse Fenster
    best, n = 0.0, len(dev_tokens)
    if n <= len(utt_tokens):
        for start in range(len(utt_tokens) - n + 1):
            ratio = SequenceMatcher(None, dev_phrase,
                                    " ".join(utt_tokens[start:start + n])).ratio()
            best = max(best, ratio)
    if best >= 0.82:
        return 50.0 + 25.0 * best, f"aehnlich({best:.2f})", 0
    return 0.0, "keins", 0


def _leftover(dev_tokens: list[str], utt_tokens: list[str]) -> int:
    """Satzwoerter, die der Geraetename nicht erklaert."""
    count = 0
    for word in utt_tokens:
        if word in dev_tokens:
            continue
        explained = False
        for dev_word in dev_tokens:
            if len(dev_word) >= 5 and len(word) >= 5 and (
                    word.startswith(dev_word) or dev_word.startswith(word)):
                shorter, longer = sorted((dev_word, word), key=len)
                if len(shorter) / len(longer) >= 0.70:
                    explained = True
                    break
        if not explained:
            count += 1
    return count


def match_devices(text: str, devices: list[dict]) -> list[Match]:
    utt_tokens = _content_tokens(text)
    if not utt_tokens:
        return []
    scored: list[Match] = []
    for dev in devices:
        dev_tokens = tokens(dev["name"])
        base, reason, _ = _score_name(dev_tokens, utt_tokens)
        if base <= 0:
            continue
        score = base - 20.0 * _leftover(dev_tokens, utt_tokens)
        if score < 45.0:
            continue
        scored.append(Match(name=dev["name"], id=dev["id"], score=score,
                            reason=reason, writable=dev.get("writable", []),
                            manufacturer=dev.get("manufacturer")))
    scored.sort(key=lambda m: (-m.score, m.name.lower()))
    out: list[Match] = []
    seen_ids: set[str] = set()
    for m in scored:
        if m.id in seen_ids:
            continue
        seen_ids.add(m.id)
        out.append(m)
    return out


# ---------------------------------------------------------------- Ergebnis

CAP_FOR = {
    "on": ["powerState", "active"],
    "off": ["powerState", "active"],
    "toggle": ["powerState", "active"],
    "percent": ["brightness", "percentage", "color-brightness"],
    "temperature": ["targetSetpoint"],
}

AMBIGUITY_MARGIN = 12.0

# Raeume, wie sie im alias-Pfad und in Geraetenamen vorkommen.
ROOMS = (
    "kueche", "schlafzimmer", "wohnzimmer", "arbeitszimmer", "badezimmer",
    "kinderzimmer", "gaestezimmer", "esszimmer", "flur", "diele", "bad",
    "buero", "keller", "garage", "garten", "terrasse", "balkon", "abstellraum",
    "hauswirtschaftsraum", "dachboden", "kino",
)


def room_in(text: str) -> str | None:
    """Welcher Raum wird genannt? Laengster Treffer gewinnt ("bad" in
    "badezimmer" darf nicht gewinnen)."""
    words = set(tokens(text))
    hits = [r for r in ROOMS if r in words]
    if not hits:
        return None
    return max(hits, key=len)


# Ein Ortsname meint immer das LICHT. Belegt: "Schlafzimmer an" soll die Lampe
# schalten, nicht die Klimaanlage. Geraete, die zwar onoff koennen, aber
# eindeutig keine Lampe sind, werden bei der Raumaufloesung ausgeschlossen.
NOT_LIGHT = (
    "klima", "heizung", "ventilator", "luefter", "steckdose", "drucker",
    "computer", "kamera", "klingel", "rollo", "jalousie", "markise", "beamer",
    "waschmaschine", "wasserkocher", "kaffeewasser", "duschwasser", "airwick",
    "rasensprenger", "gartenwasser", "regenwasser", "warmwasser", "ladezeit",
    "tibber", "stunde", "spuelung", "brause", "hausalarm", "portemonnaie",
    "schluesselfinder", "torklingel", "gateway", "solar", "backup",
)
# Woran eine Lampe erkennbar ist: sie kann mehr als nur an/aus, oder ihr Name
# sagt es direkt.
LIGHT_WORDS = ("licht", "lampe", "beleuchtung", "strahler", "ambilight",
               "led", "wled", "regenbogen", "feuer")


def is_light_device(name: str, writable: list[str]) -> bool:
    n = " " + " ".join(tokens(name)) + " "
    if any(f" {w}" in n or f"{w} " in n for w in NOT_LIGHT):
        return False
    if any(w in n for w in LIGHT_WORDS):
        return True
    # Dimmen oder Farbe koennen nur Lampen
    return bool({"brightness", "color", "colortemp"} & set(writable or []))


# Geräte, die zwar einen Sollwert führen, aber kein Raumthermostat sind.
# "Lüfter" ist der Ofen im Wohnzimmer (Setpoint 0_userdata.0.Wohnzimmer.
# ofenanaus, Istwert ofen_ist). Über "Temperatur im Wohnzimmer" darf der
# nicht gestellt werden - belegt 2026-11-07.
NOT_THERMOSTAT = ("luefter", "lufter", "ventilator", "ofen", "herd",
                  "backofen", "drucker", "steckdose", "licht", "lampe",
                  "kuehlschrank", "kaffee", "wasserkocher")


def ist_raumthermostat(name: str, writable: list[str]) -> bool:
    """Kann dieses Gerät über "Temperatur im <Raum>" gestellt werden?"""
    if "targetSetpoint" not in (writable or []):
        return False
    n = " " + " ".join(tokens(name)) + " "
    return not any(f" {w} " in n or w in n for w in NOT_THERMOSTAT)


def resolve_by_room(text: str, devices: list[dict], action: str,
                    value=None) -> "Resolution":
    """Nennt der Satz nur einen Raum, nimm das EINE Geraet darin, das die
    Aktion kann. Gibt es mehrere, wird NICHT geraten."""
    room = room_in(text)
    if not room:
        return Resolution(False, text, action=action, reason="kein-geraet")
    wanted = CAP_FOR.get(action) or CAP_FOR.get(
        "percent" if action == "relative" else action, [])
    capable, lights_in_room = [], []
    for dev in devices:
        dev_tokens = set(tokens(dev["name"]))
        # Raum muss im Geraetenamen stecken ("Lampe Kueche") oder das Geraet
        # heisst exakt wie der Raum.
        im_namen = (room in dev_tokens
                    or " ".join(tokens(dev["name"])) == room)
        # Sollwert: hier zaehlt die Raumzuordnung, nicht der Name. Ein
        # Thermostat heisst "Temperatur Klimaanlage" und traegt den Raum
        # nirgends im Namen (belegt 2026-11-07: "stelle die temperatur im
        # arbeitszimmer auf 22 grad" fand ihn nicht). Bewusst nur fuer diese
        # Aktion - bei an/aus wuerde dieselbe Regel die Raumaufloesung
        # verwaschen (viele Geraete teilen sich einen Raum).
        im_raum = (action == "temperature"
                   and normalize(dev.get("room") or "") == room
                   and ist_raumthermostat(dev["name"],
                                          dev.get("writable") or []))
        if not (im_namen or im_raum):
            continue
        if not any(c in (dev.get("writable") or []) for c in wanted):
            continue
        capable.append(dev)
        if is_light_device(dev["name"], dev.get("writable") or []):
            lights_in_room.append(dev)
    # Ortsname = Licht. Gibt es genau eine Lampe im Raum, gewinnt sie gegen
    # Schalter wie "Klima Schlafzimmer" oder "Badezimmer Heizung".
    # Bei der Sollwert-Aktion nie: dort ist die Lampe nie gemeint.
    if len(lights_in_room) == 1 and action != "temperature":
        capable = lights_in_room
    if len(capable) == 1:
        dev = capable[0]
        state = next(c for c in wanted if c in dev["writable"])
        if action == "temperature":
            # Der Sollwert kommt aus dem Satz ("... auf 22 Grad"), nicht aus
            # der Aktion. Ohne ihn waere es ein Schreibvorgang mit None.
            return Resolution(True, text, action=action, device=dev["name"],
                              device_id=dev["id"], state=state, value=value,
                              reason=f"raum:{room}")
        value_ = (True if action in ("on", "relative")
                  else False if action == "off" else None)
        return Resolution(True, text, action=action, device=dev["name"],
                          device_id=dev["id"], state=state, value=value_,
                          reason=f"raum:{room}")
    if action == "temperature" and not capable:
        # Kein Raumthermostat vorhanden - das ehrlich sagen, statt
        # "mehrdeutig" (es gibt gar keinen Kandidaten).
        return Resolution(False, text, action=action, reason="kein-thermostat",
                          candidates=[])
    return Resolution(False, text, action=action, reason="raum-mehrdeutig",
                      candidates=[d["name"] for d in (capable or lights_in_room)][:5])


def thermostat_ersatz(best, devices: list[dict]):
    """Sollwert-Geraet zu einem genannten Geraet finden.

    Belegt 2026-11-07: "stell die klimaanlage auf 22 grad" traf "Klimaanlage"
    (nur Ein/Aus). Den Sollwert fuehrt "Temperatur Klimaanlage". Ein Ersatz
    wird nur genommen, wenn GENAU EIN Sollwert-Geraet die Woerter des
    genannten Namens enthaelt - sonst waere es geraten.
    """
    wort = set(tokens(best.name))
    if not wort:
        return None
    kandidaten = []
    for dev in devices:
        if "targetSetpoint" not in (dev.get("writable") or []):
            continue
        if wort <= set(tokens(dev["name"])):
            kandidaten.append(dev)
    return kandidaten[0] if len(kandidaten) == 1 else None


@dataclass
class Resolution:
    ok: bool
    utterance: str
    action: str | None = None
    device: str | None = None
    device_id: str | None = None
    state: str | None = None
    value: object = None
    relative: int | None = None
    reason: str = ""
    candidates: list[str] = field(default_factory=list)
    margin: float = 0.0


def resolve(text: str, devices: list[dict]) -> Resolution:
    intent = parse_action(text)
    if intent.action is None:
        return Resolution(False, text, reason=intent.reason)

    matches = match_devices(text, devices)
    if not matches:
        return resolve_by_room(text, devices, intent.action, intent.value)

    best = matches[0]
    rivals = [m for m in matches[1:] if best.score - m.score < AMBIGUITY_MARGIN]
    # Wortfolge schlägt Wortmenge: steht der Gerätename zusammenhängend im
    # Satz, ist er gemeint - auch wenn ein anderer Name dieselben Wörter in
    # anderer Reihenfolge trägt. Belegt 2026-11-04: "licht arbeitszimmer an"
    # traf "Licht Arbeitszimmer" (exakt, 116) und "Arbeitszimmer Licht"
    # (nur dieselben Wörter, 106); der Abstand lag unter der Mehrdeutigkeits-
    # schwelle und der Befehl wurde abgelehnt. Vorher rettete die Lokal-Erst-
    # Regel das zufällig - eine Sonderregel nach Herkunft, die es nicht mehr
    # gibt. Diese Regel hier gilt für jedes Gerät gleich.
    if best.reason == "exakt":
        rivals = [m for m in rivals if m.reason == "exakt"]
    if rivals:
        return Resolution(False, text, action=intent.action, reason="mehrdeutig",
                          candidates=[best.name] + [m.name for m in rivals[:4]],
                          margin=best.score - rivals[0].score)

    action = intent.action
    relative = intent.relative

    # relativ: Prozent-Geraet dimmen, sonst ein/aus
    if action == "relative":
        if any(c in best.writable for c in CAP_FOR["percent"]):
            action = "percent"
        elif "active" in best.writable and "powerState" not in best.writable:
            action = "on" if (relative or 0) > 0 else "off"
            relative = None
        elif "powerState" in best.writable:
            action = "on" if (relative or 0) > 0 else "off"
            relative = None
        else:
            return Resolution(False, text, action="relative", device=best.name,
                              device_id=best.id, reason="faehigkeit-fehlt",
                              candidates=sorted(best.writable))

    state = next((c for c in CAP_FOR.get(action, []) if c in best.writable), None)
    if state is None and action == "temperature":
        # Sollwert: das genannte Geraet kann nur Ein/Aus ("Klimaanlage"),
        # den Sollwert fuehrt ein Thermostat ("Temperatur Klimaanlage").
        # Nur bei Eindeutigkeit ersetzen - sonst bleibt es beim Fehlschlag.
        ersatz = thermostat_ersatz(best, devices)
        if ersatz is not None:
            return Resolution(True, text, action="temperature",
                              device=ersatz["name"], device_id=ersatz["id"],
                              state="targetSetpoint", value=intent.value,
                              reason=f"thermostat:{best.name}")
    if state is None:
        # Falsches Geraet nicht ersetzen: der Nutzer hat dieses gemeint.
        return Resolution(False, text, action=action, device=best.name,
                          device_id=best.id, reason="faehigkeit-fehlt",
                          candidates=sorted(best.writable))

    value = None
    if action == "on":
        value = True
    elif action == "off":
        value = False
    elif action == "percent" and relative is None:
        value = float(intent.value or 0)
    elif action == "temperature":
        value = intent.value

    return Resolution(True, text, action=action, device=best.name,
                      device_id=best.id, state=state, value=value,
                      relative=relative if action == "percent" else None,
                      reason=f"treffer:{best.reason}")
