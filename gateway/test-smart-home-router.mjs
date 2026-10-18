import test from 'node:test';
import assert from 'node:assert/strict';
import { buildCatalog, classify, originalUtterance, createRouter } from './smart-home-router.mjs';

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

test('cache read failure routes to agent without writing', async () => {
  const route = createRouter({ loadCatalog: () => { throw new Error('missing'); }, send: () => assert.fail('unexpected write') });
  assert.equal(await route(args()), null);
});
test('aborted turn never writes', async () => {
  const route = createRouter({ loadCatalog: () => catalog, send: () => assert.fail('unexpected write') });
  await assert.rejects(route({ ...args(), signal: AbortSignal.abort() }));
});
