// Raum-Voranstelling fuer einfache Wohnungsbefehle.
//
// Regel des Owners: Wenn bei Licht, Steckdose oder Temperatur kein Raum
// genannt wird, wird der Raum des Geraets vorangestellt — Alexa-Gruppen
// loesen den Rest auf ("Wohnzimmer Licht an", "Wohnzimmer Licht 50 %").
//
// Bewusst NICHT umformuliert werden:
//   - Saetze, die schon einen Raum enthalten
//   - Saetze mit mehreren Aktionen, Bedingungen oder Zeitangaben
//   - Fragen und Verneinungen
//   - Ziele ausserhalb von Licht/Steckdose/Temperatur
// Im Zweifel bleibt der Satz unveraendert; dann entscheidet die KI.

const TARGET = /^(?:licht|lampe|steckdose|temperatur|heizung)$/;
const TARGET_ANY = /\b(?:licht|lampe|steckdose|temperatur|heizung)\b/;
const COMPOUND = /\b(?:und|oder|danach|dann|vorher|anschliessend|anschließend|spaeter|später|verzoegerung|verzögerung|wenn|falls|bevor|nachdem|nicht|kein|keine|ausser|außer|aber|gleichzeitig|sekunden?|minuten?|stunden?|morgen|heute)\b/;
const LEAD = /^(?:bitte\s+)?(?:(?:schalte|schalt|mach|mache|stelle|stell|dreh|drehe)\s+)?(?:(?:den|die|das)\s+)?/;

export const normalize = (text) =>
  String(text).normalize('NFC').toLocaleLowerCase('de').trim().replace(/\s+/g, ' ');

// Feste Raumwoerter, damit ein genannter Raum nicht mit dem Geraeteraum
// kollidiert ("Licht im Schlafzimmer an" darf nicht zu "Wohnzimmer Licht im
// Schlafzimmer an" werden). Bewusst breit gehalten.
export const ROOM_WORDS = [
  'wohnzimmer', 'arbeitszimmer', 'schlafzimmer', 'kinderzimmer', 'badezimmer',
  'kueche', 'küche', 'flur', 'diele', 'bad', 'buero', 'büro', 'keller',
  'garage', 'garten', 'terrasse', 'balkon', 'esszimmer', 'gaestezimmer',
  'gästezimmer', 'abstellraum', 'hauswirtschaftsraum', 'kino', 'dachboden',
];

// Praepositionalangaben sind fast immer ein genannter Ort.
const ROOM_PREPOSITION = /\b(?:im|in der|in den|am|auf dem|auf der)\s+\S+/;

export function hasRoom(text, rooms = []) {
  const s = normalize(text);
  if (ROOM_PREPOSITION.test(s)) return true;
  const all = new Set([...ROOM_WORDS, ...rooms.map(normalize)]);
  return [...all].some((room) => room && s.includes(room));
}

/**
 * Setzt den Raum vor das Zielwort, wenn keiner genannt wurde.
 * @returns {{text: string, changed: boolean, reason: string}}
 */
export function applyRoom(text, room, rooms = [room]) {
  if (typeof text !== 'string' || typeof room !== 'string' || !room.trim()) {
    return { text, changed: false, reason: 'no-room' };
  }
  const original = text.trim();
  let s = normalize(original).replace(/[.!]+$/, '').trim();

  if (!TARGET_ANY.test(s)) return { text: original, changed: false, reason: 'not-target' };
  if (COMPOUND.test(s)) return { text: original, changed: false, reason: 'compound' };
  if (/\?$/.test(original)) return { text: original, changed: false, reason: 'question' };
  if (hasRoom(s, rooms)) return { text: original, changed: false, reason: 'room-present' };

  // Fuehrendes "bitte", Verb und Artikel entfernen, dann muss das Zielwort
  // zuerst kommen. Alles andere ist zu unklar fuer eine Umformulierung.
  const stripped = s.replace(LEAD, '');
  const parts = stripped.split(' ');
  if (!TARGET.test(parts[0])) return { text: original, changed: false, reason: 'unexpected-shape' };
  // Ohne Aktion dahinter ist es kein Befehl ("Mach das Licht", "Licht").
  // Sonst entstuende "Wohnzimmer Licht" ohne An/Aus/Wert.
  if (parts.length < 2) return { text: original, changed: false, reason: 'no-action' };

  const rest = parts.slice(1).join(' ');
  // Raum und Zielwort sind Substantive und werden gross geschrieben; die
  // Aktion dahinter bleibt, wie gesprochen wurde ("an", "50 %", "blau").
  const cap = (word) => word.charAt(0).toLocaleUpperCase('de') + word.slice(1);
  const roomWord = cap(room.trim());
  const target = cap(parts[0]);
  const rebuilt = rest ? `${roomWord} ${target} ${rest}` : `${roomWord} ${target}`;

  // Satzzeichen am Ende wiederherstellen.
  const ending = /[.!]$/.test(original) ? original.slice(-1) : '';
  return { text: rebuilt + ending, changed: true, reason: 'room-added' };
}
