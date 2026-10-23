import test from 'node:test';
import assert from 'node:assert/strict';
import { buildCatalog, classify, originalUtterance, originalUtteranceBoth, createRouter, readSpokenUtterance } from './smart-home-router.mjs';

const devices = ['Monitor', 'Deckenventilator', 'Lampe Schlafzimmer', 'Lampe Küche', 'Licht', 'Steckdose Sofa', 'Hausalarm', 'Wasserkocher', 'WoW-Server', 'Heizung', 'Test', 'Lampe doppelt', 'Lampe doppelt'].map((name, i) => ({ id: String(i), name, power: true }));
const catalog = buildCatalog(devices);
const envelope = (input, transcript = input) => `<realtime_delegation>\n  <input>${input}</input>\n  <transcript_delta>user: ${transcript}</transcript_delta>\n</realtime_delegation>`;
for (const text of ['Monitor aus', 'Schalte den Monitor an.', 'Bitte schalte den Monitor aus', 'Mach das Licht an bitte', 'Schalte das Licht im Schlafzimmer aus', 'Lampe in der Küche an', 'Deckenventilator einschalten', 'Steckdose Sofa aus']) {
  test(`direct: ${text}`, () => assert.ok(classify(text, catalog)));
}
const negatives = ['Schalte Monitor aus und danach, mit Verzögerung, den Deckenventilator an.', 'Monitor aus und Deckenventilator an', 'Monitor aus danach Deckenventilator an', 'Schalte den Monitor nicht aus', 'Ist der Monitor aus?', 'Monitor aus?', 'Monitor aus in 10 Sekunden', 'Schalte den WoW-Server an', 'Hausalarm aus', 'Wasserkocher an', 'Heizung an', 'Test aus', 'Lampe doppelt an', 'Wenn ich gehe Monitor aus', 'Monitor aus oder an', 'Monitor aus aber nicht jetzt', 'Schalte ihn aus', 'Monitor aus. Deckenventilator an', 'Erkläre Monitor aus', 'Kannst du Monitor aus', 'Schalte Monitor und Licht aus', 'Monitor aus\nDeckenventilator an', 'Monitor aus falls es geht', 'Monitor aus morgen', 'Schalte den unbekannten Monitor aus'];
for (const text of negatives) test(`agent: ${text}`, () => assert.equal(classify(text, catalog), null));
test('expired/missing catalog fails closed', () => {
  assert.equal(classify('Monitor aus', { ...catalog, generatedAt: Date.now() - 49 * 3600_000 }), null);
  assert.equal(classify('Monitor aus', null), null);
});
test('complete native transcript is required, never summarized first clause', () => {
  assert.equal(originalUtterance(envelope('Monitor aus')), 'Monitor aus');
  assert.equal(originalUtterance('Monitor aus'), null);
  assert.equal(originalUtterance(envelope('Monitor aus', 'Monitor aus und danach Ventilator an')), null);
});
test('Klartext-Consult: User request ist massgeblich', () => {
  // Der live genutzte gateway-relay-Consult schickt Klartext, keinen
  // XML-Umschlag. Frueher scheiterte deshalb JEDER echte Satz.
  const real = (last, request = last) => [
    'Live voice request from the user during a browser Talk session.',
    '',
    'Recent voice transcript for context:',
    'User: Erzaehle einen Witz',
    `User: ${last}`,
    '',
    'User request:',
    request,
  ].join('\n');

  assert.equal(originalUtteranceBoth(real('Licht an')), 'Licht an');
  assert.equal(originalUtteranceBoth('User request:\nLicht an'), 'Licht an');
  // Vom Modell umformuliert oder gekuerzt: kein Direktweg.
  assert.equal(originalUtteranceBoth(real('Licht an', 'Schalte Monitor aus')), null);
  assert.equal(originalUtteranceBoth('User request:\n'), null);
});

test('XML-Form bleibt gueltig', () => {
  const xml = `<realtime_delegation>\n  <input>Licht an</input>\n  <transcript_delta>user: Licht an</transcript_delta>\n</realtime_delegation>`;
  assert.equal(originalUtteranceBoth(xml), 'Licht an');
});

test('Verlauf im Umschlag: letzte User-Zeile entscheidet', () => {
  const env = (input, transcript) =>
    `<realtime_delegation>\n  <input>${input}</input>\n  <transcript_delta>${transcript}</transcript_delta>\n</realtime_delegation>`;
  // Frueher scheiterte jeder Umschlag mit mehr als einer Zeile. Der Fehler
  // liess die Weiche nach ein paar Saetzen dauerhaft verstummen.
  assert.equal(originalUtterance(env('Licht an', 'user: Hallo\nassistant: Hi\nuser: Licht an')), 'Licht an');
  assert.equal(originalUtterance(env('Licht an', 'user: Witz\nassistant: ...\nuser: Licht an')), 'Licht an');
  // Umformulierte oder gekuerzte Auftraege bleiben gesperrt.
  assert.equal(originalUtterance(env('Licht anschalten', 'user: Licht an')), null);
  assert.equal(originalUtterance(env('Schalte Monitor aus', 'user: Schalte Monitor aus und danach den Ventilator an')), null);
  assert.equal(originalUtterance(env('', 'user: Licht an')), null);
  assert.equal(originalUtterance(env('Licht an', 'assistant: Hallo')), null);
});

const args = (text = 'Monitor aus') => ({ sessionKey: 'agent:voice:xiaozhi-realtime-v6', source: 'native-delegation', voiceSessionId: 'test-voice', question: envelope(text) });
test('concurrent duplicate calls send once and return one outcome', async () => {
  let count = 0;
  const route = createRouter({ loadCatalog: () => catalog, send: async () => { count++; return 200; }, log: () => {} });
  const results = await Promise.all(Array.from({ length: 20 }, () => route(args())));
  assert.equal(count, 1); assert.ok(results.every(r => r.text === 'An Alexa gesendet.'));
});
test('complex commands and foreign sessions cause ZERO writes', async () => {
  let count = 0;
  const route = createRouter({ loadCatalog: () => catalog, send: async () => { count++; return 200; } });
  for (const text of negatives) assert.equal(await route(args(text)), null);
  assert.equal(await route({ ...args(), sessionKey: 'agent:allgemein:main' }), null);
  assert.equal(await route({ ...args(), source: 'tool-call' }), null);
  assert.equal(await route({ ...args(), question: envelope('Monitor aus', 'Monitor aus und danach Deckenventilator an') }), null);
  assert.equal(count, 0);
});
test('timeouts and HTTP failures never fall through or retry', async () => {
  for (const failure of [async () => { throw new Error('timeout'); }, async () => 500]) {
    let count = 0;
    const route = createRouter({ loadCatalog: () => catalog, send: async () => { count++; return failure(); }, log: () => {} });
    const result = await route(args());
    assert.match(result.text, /nicht bestätigen/);
    assert.deepEqual(await route(args()), result); assert.equal(count, 1);
  }
});
test('both Jarvis voice session versions route; foreign keys never do', async () => {
  for (const key of ['agent:voice:xiaozhi-realtime-v5', 'agent:voice:xiaozhi-realtime-v6', 'agent:voice:xiaozhi-realtime-v7']) {
    let count = 0;
    const route = createRouter({ loadCatalog: () => catalog, send: async () => { count++; return 200; }, log: () => {} });
    assert.ok(await route({ ...args(), sessionKey: key }), key);
    assert.equal(count, 1, key);
  }
  for (const key of ['agent:allgemein:main', 'agent:voice:other', 'agent:voice:xiaozhi', 'agent:voice:xiaozhi-realtime-v6-extra', '', undefined]) {
    let count = 0;
    const route = createRouter({ loadCatalog: () => catalog, send: async () => { count++; return 200; } });
    assert.equal(await route({ ...args(), sessionKey: key }), null, String(key));
    assert.equal(count, 0, String(key));
  }
});

test('tool-call: nur bei exakt gesprochenem Satz', async () => {
  const fs = await import('node:fs');
  const os = await import('node:os');
  const path = await import('node:path');
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'spoken-'));
  const file = path.join(dir, 'last-utterance.json');
  const prev = process.env.OPENCLAW_SPOKEN_UTTERANCE_FILE;
  process.env.OPENCLAW_SPOKEN_UTTERANCE_FILE = file;

  const { createRouter: make } = await import('./smart-home-router.mjs');
  const setSpoken = (t) => fs.writeFileSync(file, JSON.stringify({ utterance: t, ts: Date.now() }));

  let sent = [];
  const route = make({
    loadCatalog: () => catalog,
    send: async (c) => { sent.push(c); return 200; },
    log: () => {},
  });
  const call = (asked, spoken) => {
    setSpoken(spoken);
    return route({
      sessionKey: 'agent:voice:xiaozhi-realtime-v6',
      source: 'tool-call',
      voiceSessionId: 'v1',
      question: asked,
    });
  };

  // exakt gesprochen -> direkt
  sent = [];
  assert.ok(await call('Monitor aus', 'Monitor aus'));
  assert.deepEqual(sent, ['Monitor aus']);

  // Modell kuerzt: gesprochen war mehr -> blockiert
  sent = [];
  assert.equal(await call('Monitor aus', 'Monitor aus und danach den Ventilator an'), null);
  assert.equal(sent.length, 0);

  // Modell formuliert um -> blockiert
  sent = [];
  assert.equal(await call('Schalte Monitor aus', 'Monitor aus'), null);
  assert.equal(sent.length, 0);

  // kein gesprochener Satz hinterlegt -> blockiert
  fs.unlinkSync(file);
  sent = [];
  assert.equal(await route({ sessionKey: 'agent:voice:xiaozhi-realtime-v6', source: 'tool-call', voiceSessionId: 'v1', question: 'Monitor aus' }), null);
  assert.equal(sent.length, 0);

  // abgelaufener Satz -> blockiert
  fs.writeFileSync(file, JSON.stringify({ utterance: 'Monitor aus', ts: Date.now() - 300_000 }));
  sent = [];
  assert.equal(await route({ sessionKey: 'agent:voice:xiaozhi-realtime-v6', source: 'tool-call', voiceSessionId: 'v1', question: 'Monitor aus' }), null);
  assert.equal(sent.length, 0);

  if (prev === undefined) delete process.env.OPENCLAW_SPOKEN_UTTERANCE_FILE;
  else process.env.OPENCLAW_SPOKEN_UTTERANCE_FILE = prev;
  fs.rmSync(dir, { recursive: true, force: true });
});

test('fremde Quelle wird weiterhin abgewiesen', async () => {
  const route = createRouter({ loadCatalog: () => catalog, send: () => assert.fail('kein Senden'), log: () => {} });
  for (const source of ['reply', 'other', '', undefined]) {
    assert.equal(await route({ ...args(), source }), null, String(source));
  }
});

test('cache read failure routes to agent without writing', async () => {
  const route = createRouter({ loadCatalog: () => { throw new Error('missing'); }, send: () => assert.fail('unexpected write') });
  assert.equal(await route(args()), null);
});
test('aborted turn never writes', async () => {
  const route = createRouter({ loadCatalog: () => catalog, send: () => assert.fail('unexpected write') });
  await assert.rejects(route({ ...args(), signal: AbortSignal.abort() }));
});
