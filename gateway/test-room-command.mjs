import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { applyRoom, hasRoom, readActiveRoom } from './room-command.mjs';

const ROOMS = ['Wohnzimmer', 'Arbeitszimmer', 'Schlafzimmer', 'Küche', 'Badezimmer'];

const expectChange = (input, expected, room = 'Wohnzimmer') => {
  const r = applyRoom(input, room, ROOMS);
  assert.equal(r.changed, true, `sollte umgestellt werden: ${input} (${r.reason})`);
  assert.equal(r.text, expected, input);
};

// --- soll umstellen -------------------------------------------------------
test('Licht an -> Wohnzimmer Licht an', () => expectChange('Licht an', 'Wohnzimmer Licht an'));
test('licht aus', () => expectChange('licht aus', 'Wohnzimmer Licht aus'));
test('Schalte das Licht an', () => expectChange('Schalte das Licht an', 'Wohnzimmer Licht an'));
test('bitte Licht an', () => expectChange('bitte Licht an', 'Wohnzimmer Licht an'));
test('Bitte schalte das Licht aus', () => expectChange('Bitte schalte das Licht aus', 'Wohnzimmer Licht aus'));
test('mit Punkt', () => expectChange('Mach das Licht an.', 'Wohnzimmer Licht an.'));
test('Steckdose an', () => expectChange('Steckdose an', 'Wohnzimmer Steckdose an'));
test('Steckdose aus', () => expectChange('schalte die Steckdose aus', 'Wohnzimmer Steckdose aus'));
test('Licht ein -> an', () => expectChange('Licht ein', 'Wohnzimmer Licht an'));
test('Arbeitszimmer', () => expectChange('Licht an', 'Arbeitszimmer Licht an', 'Arbeitszimmer'));
test('Küche mit Umlaut', () => expectChange('Licht aus', 'Küche Licht aus', 'Küche'));

// --- darf nicht umstellen -------------------------------------------------
const unchanged = [
  'Licht Wohnzimmer an',
  'Schalte das Licht im Wohnzimmer aus',
  'Licht im Schlafzimmer an',
  'Wegbeleuchtung an',
  'Monitorlicht an',
  'Monitor aus',
  'Licht an und Steckdose aus',
  'Licht an wenn ich komme',
  'Licht an in 10 Sekunden',
  'Ist das Licht an?',
  'Licht nicht an',
  'Erzähle einen Witz',
  'Licht 50 %',
  'Licht blau',
  'Temperatur',
  'Licht',
  'Licht irgendwas',
  'Schalte den WoW-Server an',
];
for (const text of unchanged) {
  test(`unveraendert: ${text}`, () => {
    const r = applyRoom(text, 'Wohnzimmer', ROOMS);
    assert.equal(r.changed, false, `unerwartet umgestellt: ${r.text} (${r.reason})`);
    assert.equal(r.text, text);
  });
}

// --- Randfaelle -----------------------------------------------------------
test('ohne Raum keine Umstellung', () => {
  const r = applyRoom('Licht an', '', ROOMS);
  assert.equal(r.changed, false);
  assert.equal(r.reason, 'no-room');
});

test('idempotent', () => {
  const once = applyRoom('Licht an', 'Wohnzimmer', ROOMS);
  const twice = applyRoom(once.text, 'Wohnzimmer', ROOMS);
  assert.equal(twice.changed, false);
  assert.equal(twice.text, once.text);
});

test('hasRoom erkennt Umlaut', () => assert.equal(hasRoom('licht in der küche an', ROOMS), true));
test('hasRoom erkennt Grossschreibung', () => assert.equal(hasRoom('LICHT WOHNZIMMER AN', ROOMS), true));
test('hasRoom ohne Raum', () => assert.equal(hasRoom('Licht an', ROOMS), false));
test('hasRoom mit Praeposition', () => assert.equal(hasRoom('Licht auf dem Balkon an', ROOMS), true));

// --- aktiver Raum ---------------------------------------------------------
const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'room-'));
const activeFile = path.join(dir, 'active-room.json');

test('aktiver Raum wird gelesen', () => {
  fs.writeFileSync(activeFile, JSON.stringify({ room: 'Wohnzimmer', ts: Date.now() }));
  assert.equal(readActiveRoom(activeFile), 'Wohnzimmer');
});

test('abgelaufener Raum wird ignoriert', () => {
  fs.writeFileSync(activeFile, JSON.stringify({ room: 'Wohnzimmer', ts: Date.now() - 400_000 }));
  assert.equal(readActiveRoom(activeFile), null);
});

test('Zeitstempel in Sekunden (Python) wird gelesen', () => {
  // Die Bridge schreibt mit time.time() in Sekunden, JavaScript rechnet in
  // Millisekunden. Ohne Umrechnung galt jeder Raum als abgelaufen.
  fs.writeFileSync(activeFile, JSON.stringify({ room: 'Wohnzimmer', ts: Math.floor(Date.now() / 1000) }));
  assert.equal(readActiveRoom(activeFile), 'Wohnzimmer');
});

test('Zeitstempel in Millisekunden wird gelesen', () => {
  fs.writeFileSync(activeFile, JSON.stringify({ room: 'Wohnzimmer', ts: Date.now() }));
  assert.equal(readActiveRoom(activeFile), 'Wohnzimmer');
});

test('fehlende Datei ergibt null', () => {
  assert.equal(readActiveRoom(path.join(dir, 'fehlt.json')), null);
});

test('kaputte Datei ergibt null', () => {
  fs.writeFileSync(activeFile, '{kaputt');
  assert.equal(readActiveRoom(activeFile), null);
});

test('Raum aus der Zukunft wird ignoriert', () => {
  fs.writeFileSync(activeFile, JSON.stringify({ room: 'Wohnzimmer', ts: Date.now() + 600_000 }));
  assert.equal(readActiveRoom(activeFile), null);
});

test.after(() => fs.rmSync(dir, { recursive: true, force: true }));
