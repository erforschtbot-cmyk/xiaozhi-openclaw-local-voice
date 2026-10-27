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
UP_WORDS = {"hoch", "rauf", "heller", "auf", "oeffne", "oeffnen", "aufmachen",
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


# ---------------------------------------------------------------- Aktion

@dataclass
class Intent:
    action: str | None = None          # on | off | toggle | percent | temperature
    value: float | None = None
    relative: int | None = None        # +10 / -10 bei "heller"/"dunkler"
    reason: str = ""


def _number(text: str) -> int | None:
    m = re.search(r"\b(\d{1,3})\b", normalize(text))
    return int(m.group(1)) if m else None


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
        for size in (2, 3):
            if size > len(utt_tokens):
                continue
            for start in range(len(utt_tokens) - size + 1):
                if "".join(utt_tokens[start:start + size]) == dev_joined:
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


def resolve_by_room(text: str, devices: list[dict], action: str) -> "Resolution":
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
        if room not in dev_tokens and " ".join(tokens(dev["name"])) != room:
            continue
        if not any(c in (dev.get("writable") or []) for c in wanted):
            continue
        capable.append(dev)
        if is_light_device(dev["name"], dev.get("writable") or []):
            lights_in_room.append(dev)
    # Ortsname = Licht. Gibt es genau eine Lampe im Raum, gewinnt sie gegen
    # Schalter wie "Klima Schlafzimmer" oder "Badezimmer Heizung".
    if len(lights_in_room) == 1:
        capable = lights_in_room
    if len(capable) == 1:
        dev = capable[0]
        state = next(c for c in wanted if c in dev["writable"])
        value = (True if action in ("on", "relative")
                 else False if action == "off" else None)
        return Resolution(True, text, action=action, device=dev["name"],
                          device_id=dev["id"], state=state, value=value,
                          reason=f"raum:{room}")
    return Resolution(False, text, action=action, reason="raum-mehrdeutig",
                      candidates=[d["name"] for d in (capable or lights_in_room)][:5])


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
        return resolve_by_room(text, devices, intent.action)

    best = matches[0]
    rivals = [m for m in matches[1:] if best.score - m.score < AMBIGUITY_MARGIN]
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
