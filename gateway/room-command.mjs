// Raum-Voranstellung fuer einfache Wohnungsbefehle.
//
// Nur zwei Ziele, nur an/aus:
//   "Licht an"     -> "Wohnzimmer Licht an"
//   "Steckdose an" -> "Wohnzimmer Steckdose an"
//
// Alexa loest den Rest ueber ihre Smart-Home-Gruppe auf. Alles andere bleibt
// unangetastet: eigene Geraetenamen (Wegbeleuchtung, Monitorlicht), genannter
// Raum, mehrere Aktionen, Bedingungen, Zeitangaben, Fragen, Verneinungen.
//
// Der Raum kommt aus active-room.json, das die Bridge beim Verbindungsaufbau
// aus der Device-Id (MAC) schreibt.

import fs from 'node:fs';

export const ACTIVE_ROOM = '/home/openclaw/.local/share/jarvis-realtime-bridge/active-room.json';
const ACTIVE_ROOM_TTL_MS = 300_000;

const TARGETS = new Set(['licht', 'lichter', 'steckdose', 'steckdosen']);
// Ausgabe immer in der Einzahl: Alexa-Gruppenbefehle lauten "Licht an".
const CANONICAL = new Map([['licht', 'licht'], ['lichter', 'licht'],
  ['steckdose', 'steckdose'], ['steckdosen', 'steckdose']]);
const ACTIONS = new Map([['an', 'an'], ['ein', 'an'], ['aus', 'aus']]);

const COMPOUND = /\b(?:und|oder|danach|dann|vorher|anschliessend|anschließend|spaeter|später|wenn|falls|bevor|nachdem|nicht|kein|keine|ausser|außer|aber|gleichzeitig|sekunden?|minuten?|stunden?|morgen|heute)\b/;

const LEAD = /^(?:bitte\s+)?(?:(?:schalte|schalt|mach|mache|stelle|stell)\s+)?(?:(?:den|die|das)\s+)?/;

const ROOM_WORDS = new Set([
  'wohnzimmer', 'arbeitszimmer', 'schlafzimmer', 'kinderzimmer', 'badezimmer',
  'kueche', 'küche', 'flur', 'diele', 'bad', 'buero', 'büro', 'keller',
  'garage', 'garten', 'terrasse', 'balkon', 'esszimmer', 'gaestezimmer',
  'gästezimmer', 'abstellraum', 'hauswirtschaftsraum', 'kino', 'dachboden',
]);

const ROOM_PREPOSITION = /\b(?:im|in der|in den|am|auf dem|auf der)\s+\S+/;

export const normalize = (text) =>
  String(text).normalize('NFC').toLocaleLowerCase('de').trim().replace(/\s+/g, ' ');

export function hasRoom(text, extraRooms = []) {
  const s = normalize(text);
  if (ROOM_PREPOSITION.test(s)) return true;
  const words = new Set([...ROOM_WORDS, ...extraRooms.map(normalize).filter(Boolean)]);
  for (const room of words) if (room && s.includes(room)) return true;
  return false;
}

/**
 * Setzt den Raum vor das Zielwort.
 * @returns {{text: string, changed: boolean, reason: string}}
 */
export function applyRoom(text, room, extraRooms = []) {
  if (typeof text !== 'string' || typeof room !== 'string' || !room.trim()) {
    return { text, changed: false, reason: 'no-room' };
  }

  const original = text.trim();
  const s = normalize(original).replace(/[.!?]+$/, '').trim();

  if (COMPOUND.test(s)) return { text: original, changed: false, reason: 'compound' };
  if (hasRoom(s, extraRooms)) return { text: original, changed: false, reason: 'room-present' };
  if (original.endsWith('?')) return { text: original, changed: false, reason: 'question' };

  const stripped = s.replace(LEAD, '').split(' ');
  if (stripped.length !== 2) return { text: original, changed: false, reason: 'unexpected-shape' };

  const [target, action] = stripped;
  if (!TARGETS.has(target)) return { text: original, changed: false, reason: 'not-target' };
  if (!ACTIONS.has(action)) return { text: original, changed: false, reason: 'unknown-action' };

  const cap = (w) => w.charAt(0).toLocaleUpperCase('de') + w.slice(1);
  const roomWord = cap(room.trim());
  const body = `${roomWord} ${cap(CANONICAL.get(target))} ${ACTIONS.get(action)}`;
  const ending = /[.!?]$/.test(original) ? original.slice(-1) : '';
  return { text: body + ending, changed: true, reason: 'room-added' };
}

/**
 * Raum des zuletzt verbundenen Geraets, oder null wenn unklar/abgelaufen.
 * Bei fehlender oder kaputter Datei wird bewusst nicht geraten.
 */
export function readActiveRoom(path = ACTIVE_ROOM, now = Date.now()) {
  let data;
  try {
    data = JSON.parse(fs.readFileSync(path, 'utf8'));
  } catch {
    return null;
  }
  const room = data?.room;
  let ts = Number(data?.ts);
  if (typeof room !== 'string' || !room.trim()) return null;
  if (!Number.isFinite(ts)) return null;
  // Python schreibt Sekunden, JavaScript rechnet in Millisekunden. Werte
  // unterhalb von 1e12 sind Sekunden und werden umgerechnet.
  if (ts < 1e12) ts *= 1000;
  if (now - ts > ACTIVE_ROOM_TTL_MS || ts > now + 60_000) return null;
  return room.trim();
}
