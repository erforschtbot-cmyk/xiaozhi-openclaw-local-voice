"""STT-Fehler korrigieren, bevor der Befehl ausgewertet wird.

Spracherkennung verhoert sich bei Namen: "Küche" -> "Kuchen", "Monitorlicht"
-> "Monitorlicht" bleibt, "Deckenstrahler" -> "Deckenstrahl". Ein LLM wuerde das
erkennen, kostet aber Sekunden. Hier wird gegen ein festes Vokabular korrigiert,
das aus den echten Objekten stammt (Raeume + Geraetenamen).

Grenze: korrigiert wird nur, wenn das Wort dem Ziel SEHR aehnlich ist. Sonst
bleibt es unveraendert und der Aufrufer fragt nach. Nie raten.
"""
from __future__ import annotations

import unicodedata

# Ab dieser Aehnlichkeit gilt ein STT-Fehler als erkannt.
# 0.78 faengt "kuche"->"kueche" (0.83) und "Schlafzimer"->"Schlafzimmer" (0.92),
# laesst aber "keller"->"kueche" (0.33) unangetastet.
MIN_RATIO = 0.78

# ERWARTETE Verwechslungen — bewusst NICHT als "beobachtet" bezeichnet.
#
# Stand 2026-10-27: Es liegt KEIN echter Verhoerer aus dem Betrieb vor. Der
# Eigentuemer hat "Kuchen statt Kueche" nur als Beispiel genannt, um zu
# erklaeren, was er mit unzuverlaessigem STT meint. Diese Tabelle ist daher
# eine Vorbereitung, keine Sammlung gemessener Fehler.
#
# Warum die Tabelle ueberhaupt noetig ist: "Kuchen" und "Kueche" liegen
# rechnerisch nur bei 0.67. Eine niedrigere Aehnlichkeitsschwelle wuerde auch
# "Keller" oder "Kissen" faelschlich auf "Kueche" ziehen.
#
# Nicht aufgenommen, weil zu weit hergeholt und ohne jeden Beleg:
#   wolfzimmer, gartenbeleutung, kuchenblech, kuechenzeile
# Solche Eintraege waeren geraten und koennten echte Saetze falsch verbiegen.
#
# Regel fuer Erweiterungen: nur Eintraege, die im Betrieb TATSAECHLICH aufgetreten
# sind, mit Datum im Kommentar. Sonst waechst hier eine Wunschliste statt einer
# Fehlerliste.
KNOWN_CONFUSIONS = {
    # Beispiel des Eigentuemers, nicht beobachtet:
    "kuchen": "Küche",

    # Schreibweisen ohne Umlaut (Tastatur, Diktat, alte Geraete) — plausibel:
    "kuche": "Küche",

    # Doppelkonsonant/Vertauschung, bei deutschen Zerlegern haeufig:
    "wohnzimer": "Wohnzimmer",
    "schlafzimer": "Schlafzimmer",
    "arbeitszimer": "Arbeitszimmer",
    "badezimer": "Badezimmer",
    "badzimmer": "Badezimmer",

    # Geraetenamen, die als ein Wort gesprochen werden:
    "monitorlich": "Monitorlicht",
    "deckentstrahler": "Deckenstrahler",
    "steckdosen": "Steckdose",
}


def _norm(word: str) -> str:
    s = unicodedata.normalize("NFC", str(word or "")).lower()
    s = s.replace("ß", "ss").replace("ä", "ae").replace("ö", "oe").replace("ü", "ue")
    return "".join(ch for ch in s if ch.isalnum())


def distance(a: str, b: str) -> int:
    """Damerau-Levenshtein: zaehlt Vertauschungen als einen Fehler.

    "kuchee" statt "kueche" ist auf der Tastatur ein Vertauscher, kein
    Loeschvorgang — ohne diese Variante waere der Abstand zu gross.
    """
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev2 = None
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cost = 0 if ca == cb else 1
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost))
            if (prev2 and i > 1 and j > 1 and ca == b[j - 2] and a[i - 2] == cb):
                cur[j] = min(cur[j], prev2[j - 2] + 1)
        prev2, prev = prev, cur
    return prev[len(b)]


def ratio(a: str, b: str) -> float:
    a, b = _norm(a), _norm(b)
    if not a or not b:
        return 0.0
    longest = max(len(a), len(b))
    return 1.0 - distance(a, b) / longest


class Vocabulary:
    """Woerter, die in einem Smart-Home-Satz vorkommen koennen."""

    def __init__(self, words):
        # laengste zuerst: bei Gleichstand gewinnt das spezifischere Wort
        self.words = sorted({w for w in words if w}, key=lambda w: (-len(w), w))
        self._norm = {w: _norm(w) for w in self.words}

    def correct(self, token: str) -> tuple[str, bool, float]:
        """Gibt (korrigiertes Wort, wurde_geaendert, aehnlichkeit) zurueck."""
        t = _norm(token)
        if not t:
            return token, False, 0.0
        # 1) bekannte Verwechslung: schlaegt jede Aehnlichkeitsrechnung
        known = KNOWN_CONFUSIONS.get(t)
        if known:
            return known, _norm(known) != t, 1.0
        if t in self._norm.values():
            for w, n in self._norm.items():
                if n == t:
                    return w, False, 1.0
        best, best_r = token, 0.0
        for w, n in self._norm.items():
            # Laengenfilter spart Zeit und verhindert Unsinnstreffer
            if abs(len(n) - len(t)) > max(2, len(n) // 3):
                continue
            r = ratio(t, n)
            if r > best_r:
                best, best_r = w, r
        if best_r >= MIN_RATIO:
            return best, _norm(best) != t, best_r
        return token, False, best_r


def correct_sentence(text: str, vocab: Vocabulary) -> tuple[str, list[tuple[str, str, float]]]:
    """Korrigiert jedes Wort einzeln. Gibt Satz und Liste der Aenderungen."""
    parts = str(text or "").split()
    out, changes = [], []
    for word in parts:
        fixed, changed, r = vocab.correct(word)
        out.append(fixed)
        if changed:
            changes.append((word, fixed, r))
    return " ".join(out), changes
