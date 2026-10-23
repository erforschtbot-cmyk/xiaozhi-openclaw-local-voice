"""Raum-Voranstellung — bewusst klein.

Nur zwei Ziele, nur an/aus:
  "Licht an"     -> "Wohnzimmer Licht an"
  "Licht aus"    -> "Wohnzimmer Licht aus"
  "Steckdose an" -> "Wohnzimmer Steckdose an"

Erlaubte Fuellwoerter davor: "bitte", Verben (schalte/schalt/mach/mache),
Artikel (den/die/das). Alexa loest dann ueber die Gruppe auf.

Nicht umgestellt wird alles andere: eigene Geraetenamen (Wegbeleuchtung,
Monitorlicht), genannter Raum, mehrere Aktionen, Bedingungen, Zeitangaben,
Fragen, Verneinungen. Im Zweifel unveraendert an die KI.
"""
from __future__ import annotations

import re
import unicodedata
import json
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent
ROOM_MAP = BASE / "room-map.json"
ACTIVE_ROOM = BASE / "active-room.json"
# Ein Raum gilt nur kurz. Ein alter Eintrag darf nie ein anderes Geraet
# umbenennen, deshalb laeuft er nach dieser Zeit ab.
ACTIVE_ROOM_TTL = 300.0

TARGETS = {"licht", "lichter", "steckdose", "steckdosen"}
# Ausgabe immer in der Einzahl: Alexa-Gruppenbefehle lauten "Licht an",
# nicht "Lichter an".
CANONICAL = {"licht": "licht", "lichter": "licht",
             "steckdose": "steckdose", "steckdosen": "steckdose"}
ACTIONS = {"an": "an", "ein": "an", "aus": "aus"}

COMPOUND = re.compile(
    r"\b(?:und|oder|danach|dann|vorher|anschliessend|anschließend|spaeter|später|"
    r"wenn|falls|bevor|nachdem|nicht|kein|keine|ausser|außer|aber|"
    r"gleichzeitig|sekunden?|minuten?|stunden?|morgen|heute)\b"
)

LEAD = re.compile(
    r"^(?:bitte\s+)?(?:(?:schalte|schalt|mach|mache|stelle|stell)\s+)?"
    r"(?:(?:den|die|das)\s+)?"
)

ROOM_WORDS = {
    "wohnzimmer", "arbeitszimmer", "schlafzimmer", "kinderzimmer", "badezimmer",
    "kueche", "küche", "flur", "diele", "bad", "buero", "büro", "keller",
    "garage", "garten", "terrasse", "balkon", "esszimmer", "gaestezimmer",
    "gästezimmer", "abstellraum", "hauswirtschaftsraum", "kino", "dachboden",
}

ROOM_PREPOSITION = re.compile(r"\b(?:im|in der|in den|am|auf dem|auf der)\s+\S+")


def normalize(text: str) -> str:
    return " ".join(unicodedata.normalize("NFC", str(text)).lower().split())


def has_room(text: str, extra_rooms=()) -> bool:
    s = normalize(text)
    if ROOM_PREPOSITION.search(s):
        return True
    words = set(ROOM_WORDS) | {normalize(r) for r in extra_rooms if r}
    return any(room and room in s for room in words)


def apply_room(text: str, room: str, extra_rooms=()):
    """Rueckgabe: (text, geaendert, grund)."""
    if not isinstance(text, str) or not isinstance(room, str) or not room.strip():
        return text, False, "no-room"

    original = text.strip()
    s = re.sub(r"[.!?]+$", "", normalize(original)).strip()

    if COMPOUND.search(s):
        return original, False, "compound"
    if has_room(s, extra_rooms):
        return original, False, "room-present"
    if original.endswith("?"):
        return original, False, "question"

    stripped = LEAD.sub("", s).split()
    if len(stripped) != 2:
        return original, False, "unexpected-shape"

    target, action = stripped
    if target not in TARGETS:
        return original, False, "not-target"
    if action not in ACTIONS:
        return original, False, "unknown-action"

    room_word = room.strip()
    room_word = room_word[:1].upper() + room_word[1:]
    word = CANONICAL[target]
    body = f"{room_word} {word[:1].upper()}{word[1:]} {ACTIONS[action]}"
    ending = original[-1] if original[-1] in ".!?" else ""
    return body + ending, True, "room-added"


# ---------------------------------------------------------------------------
# Geraet -> Raum, und der zuletzt aktive Raum fuer die Weiche.
# ---------------------------------------------------------------------------

def normalize_mac(value: str) -> str:
    return str(value or "").strip().lower().replace("_", ":").replace("-", ":")


def load_room_map(path=ROOM_MAP) -> dict:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    rooms = data.get("rooms") if isinstance(data, dict) else None
    if not isinstance(rooms, dict):
        return {}
    return {normalize_mac(k): v for k, v in rooms.items() if isinstance(v, str) and v.strip()}


def room_for_device(device_id, path=ROOM_MAP):
    """Raum des Geraets oder None. Unbekannt bleibt unbekannt — kein Raten."""
    if not device_id:
        return None
    return load_room_map(path).get(normalize_mac(device_id))


def publish_active_room(device_id, room, path=ACTIVE_ROOM):
    """Merkt den Raum des gerade verbundenen Geraets fuer die Weiche."""
    if not room:
        return False
    payload = {"device_id": normalize_mac(device_id), "room": room, "ts": time.time()}
    try:
        tmp = Path(f"{path}.{time.time_ns()}.tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False) + "\n", encoding="utf-8")
        tmp.replace(path)
        return True
    except OSError:
        return False


def read_active_room(path=ACTIVE_ROOM, now=None):
    """Aktueller Raum oder None. Abgelaufene oder kaputte Datei = None."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        room = data.get("room")
        ts = float(data.get("ts", 0))
    except (OSError, ValueError, TypeError):
        return None
    if not isinstance(room, str) or not room.strip():
        return None
    now = time.time() if now is None else now
    if now - ts > ACTIVE_ROOM_TTL or ts > now + 60:
        return None
    return room.strip()
