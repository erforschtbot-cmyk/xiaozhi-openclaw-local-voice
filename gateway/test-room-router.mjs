import test from 'node:test';
import assert from 'node:assert/strict';
import { applyRoom, hasRoom } from './room-router.mjs';

const ROOMS = ['Wohnzimmer', 'Arbeitszimmer', 'Schlafzimmer', 'Küche', 'Badezimmer'];

const expectPrefix = (input, expected, room = 'Wohnzimmer') => {
  const r = applyRoom(input, room, ROOMS);
  assert.equal(r.changed, true, `should change: ${input}`);
  assert.equal(r.text, expected, input);
};

// --- soll den Raum voranstellen -------------------------------------------
test('Licht an -> Wohnzimmer Licht an', () => expectPrefix('Licht an', 'Wohnzimmer Licht an'));
test('licht aus -> Wohnzimmer Licht aus', () => expectPrefix('licht aus', 'Wohnzimmer Licht aus'));
test('Schalte das Licht an', () => expectPrefix('Schalte das Licht an', 'Wohnzimmer Licht an'));
test('Mach das Licht aus.', () => expectPrefix('Mach das Licht aus.', 'Wohnzimmer Licht aus.'));
test('Bitte Licht an', () => expectPrefix('Bitte Licht an', 'Wohnzimmer Licht an'));
test('Licht 50 %', () => expectPrefix('Licht 50 %', 'Wohnzimmer Licht 50 %'));
test('Licht 50%', () => expectPrefix('Licht 50%', 'Wohnzimmer Licht 50%'));
test('Licht blau', () => expectPrefix('Licht blau', 'Wohnzimmer Licht blau'));
test('Licht gelb', () => expectPrefix('Licht gelb', 'Wohnzimmer Licht gelb'));
test('Licht heller', () => expectPrefix('Licht heller', 'Wohnzimmer Licht heller'));
test('Licht dunkler', () => expectPrefix('Licht dunkler', 'Wohnzimmer Licht dunkler'));
test('Steckdose an', () => expectPrefix('Steckdose an', 'Wohnzimmer Steckdose an'));
test('Lampe aus', () => expectPrefix('Lampe aus', 'Wohnzimmer Lampe aus'));
test('Temperatur 21', () => expectPrefix('Temperatur 21', 'Wohnzimmer Temperatur 21'));

// --- darf NICHT umformuliert werden ---------------------------------------
const unchanged = [
  'Licht Wohnzimmer an',          // Raum schon genannt
  'Schalte das Licht im Wohnzimmer aus',
  'Licht im Arbeitszimmer an',
  'Schalte Licht an und danach die Steckdose aus',  // mehrere Aktionen
  'Licht an wenn ich komme',      // Bedingung
  'Licht an in 10 Sekunden',      // Zeit
  'Ist das Licht an?',            // Frage
  'Licht nicht an',               // Verneinung
  'Schalte den Monitor aus',      // kein Ziel dieser Regel
  'Erzähle einen Witz',           // gar kein Befehl
  'Wie warm ist es?',             // Frage
  'Schalte den WoW-Server an',    // anderes Ziel
  'Licht',                        // nur Zielwort, keine Aktion
  'Mach das Licht',               // keine Aktion
];
for (const text of unchanged) {
  test(`unveraendert: ${text}`, () => {
    const r = applyRoom(text, 'Wohnzimmer', ROOMS);
    assert.equal(r.changed, false, `unexpected change: ${r.text}`);
    assert.equal(r.text, text, 'must return original text');
  });
}

// --- Gegenprobe: andere Raeume --------------------------------------------
test('anderer Raum: Arbeitszimmer', () => expectPrefix('Licht an', 'Arbeitszimmer Licht an', 'Arbeitszimmer'));
test('anderer Raum: Kueche', () => expectPrefix('Licht aus', 'Küche Licht aus', 'Küche'));

// --- Randfaelle ------------------------------------------------------------
test('kein Raum konfiguriert', () => {
  const r = applyRoom('Licht an', '', ROOMS);
  assert.equal(r.changed, false);
  assert.equal(r.text, 'Licht an');
});
test('leerer Text', () => {
  const r = applyRoom('', 'Wohnzimmer', ROOMS);
  assert.equal(r.changed, false);
});
test('Raum mit Umlaut wird erkannt', () => assert.equal(hasRoom('licht in der küche an', ROOMS), true));
test('Raumname in anderer Schreibweise erkannt', () => assert.equal(hasRoom('LICHT WOHNZIMMER AN', ROOMS), true));
test('kein Raum enthalten', () => assert.equal(hasRoom('Licht an', ROOMS), false));

// --- Doppelte Anwendung darf nicht doppelt praefixen ----------------------
test('idempotent: zweite Anwendung aendert nichts', () => {
  const once = applyRoom('Licht an', 'Wohnzimmer', ROOMS);
  const twice = applyRoom(once.text, 'Wohnzimmer', ROOMS);
  assert.equal(twice.changed, false);
  assert.equal(twice.text, once.text);
});
