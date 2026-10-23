import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import { buildCatalog } from './smart-home-router.mjs';
import { createScopedRouter } from './scoped-smart-home-router.mjs';
import { voiceTurnStorage, bindVoiceSession, readVoiceBinding, unbindVoiceSession } from './voice-turn-context.mjs';

const sessionKey = 'agent:voice:xiaozhi-realtime-v6';
const catalog = buildCatalog([{ id: 'monitor', name: 'Monitor', power: true }]);
const turn = (utterance = 'Licht an', room = 'Schlafzimmer', id = 't1', voice = 'voice-1') => ({
  utterance, room, turnId: id, voiceSessionId: voice, sessionKey, deviceId: 'device-' + voice, observedAt: Date.now()
});
const args = t => ({ sessionKey, voiceSessionId: t.voiceSessionId, source: 'native-delegation', question: t.utterance, turn: t });
function harness(overrides = {}) {
  const sent = [], logs = [];
  const route = createScopedRouter({ loadCatalog: () => catalog, send: async text => { sent.push(text); return 200; }, log: line => logs.push(line), ...overrides });
  return { route, sent, logs };
}

test('parallel devices retain independent rooms', async () => {
  const h = harness();
  await Promise.all([h.route(args(turn())), h.route(args(turn('Licht aus', 'Arbeitszimmer', 't2', 'voice-2')))]);
  assert.deepEqual(h.sent, ['Schlafzimmer Licht an', 'Arbeitszimmer Licht aus']);
});
test('same turn sends once; new turn sends again', async () => {
  const h = harness(), a = args(turn('Monitor aus'));
  const results = await Promise.all(Array.from({ length: 12 }, () => h.route(a)));
  assert.equal(h.sent.length, 1); assert.ok(results.every(r => r.text === results[0].text));
  await h.route(args(turn('Monitor aus', 'Schlafzimmer', 'next')));
  assert.equal(h.sent.length, 2);
});
for (const text of ['Monitor aus und danach Ventilator an', 'Schalte Monitor nicht aus', 'Ist der Monitor aus?', 'Monitor aus in 10 Sekunden', 'Wenn ich gehe Monitor aus', 'Unbekanntes Gerät an']) {
  test(`agent only: ${text}`, async () => {
    const h = harness(); assert.equal(await h.route(args(turn(text))), null); assert.equal(h.sent.length, 0);
    assert.match(h.logs.at(-1), /not-single-known-command/);
  });
}
for (const change of [a => { a.turn = undefined; }, a => { a.turn.deviceId = ''; }, a => { a.turn.voiceSessionId = 'other'; }, a => { a.turn.observedAt -= 130000; }, a => { a.question = 'Monitor aus'; }, a => { a.sessionKey = 'agent:allgemein:main'; }]) {
  test(`unbound/stale/mismatched request ${change}`, async () => {
    const h = harness(), a = args(turn('Monitor aus und danach Ventilator an')); change(a);
    assert.equal(await h.route(a), null); assert.equal(h.sent.length, 0); assert.match(h.logs.at(-1), /skipped reason=/);
  });
}
test('wrapped request still checked against independent full transcript', async () => {
  const h = harness(), t = turn('Monitor aus');
  await h.route({ ...args(t), question: '<realtime_delegation><input>Monitor aus</input></realtime_delegation>' });
  assert.deepEqual(h.sent, ['Monitor aus']);
  const complex = turn('Monitor aus und Licht an', '', 'complex');
  assert.equal(await h.route({ ...args(complex), question: '<realtime_delegation><input>Monitor aus</input><transcript_delta>user: Monitor aus</transcript_delta></realtime_delegation>' }), null);
});
test('missing room does not use global active-room file', async () => {
  const h = harness(); assert.equal(await h.route(args(turn('Licht an', null))), null); assert.equal(h.sent.length, 0);
});
test('stale catalog blocks even room-group direct commands', async () => {
  const h = harness({ loadCatalog: () => ({ ...catalog, generatedAt: Date.now() - 49 * 3600_000 }) });
  assert.equal(await h.route(args(turn())), null); assert.equal(h.sent.length, 0);
});
test('uncertain send never retries or falls through', async () => {
  let writes = 0;
  const h = harness({ send: async () => { writes++; throw new Error('timeout'); } }), a = args(turn());
  assert.match((await h.route(a)).text, /nicht bestätigen/);
  await h.route(a); assert.equal(writes, 1);
});
test('aborted request does not write', async () => {
  const h = harness(); await assert.rejects(h.route({ ...args(turn()), signal: AbortSignal.abort() })); assert.equal(h.sent.length, 0);
});
test('bindings are separated by actual voice-session UUID', () => {
  const id = 'integration-binding-test-20261019';
  try {
    bindVoiceSession(id, sessionKey, 'device-a', 'Schlafzimmer');
    assert.equal(readVoiceBinding(id, sessionKey).room, 'Schlafzimmer');
    assert.equal(readVoiceBinding(id, 'other'), null);
    assert.equal(readVoiceBinding('../escape', sessionKey), null);
  } finally { unbindVoiceSession(id); }
});

test('ACTUAL gateway entry: direct skips agent, complex calls agent with scoped room', async () => {
  // Resolve the live handler by content, never by filename: the file name
  // changes with every OpenClaw release (2026.10.4 -> De0Uc7cE, 2026.10.5 ->
  // xUkepsRi, ...). The marker file is the one that carries the Jarvis router.
  const distDir = '/home/openclaw/.npm-global/lib/node_modules/openclaw/dist';
  const candidates = fs.readdirSync(distDir).filter(name => /^handlers-.*\.mjs$/.test(name));
  const handlerName = candidates.find(name =>
    fs.readFileSync(`${distDir}/${name}`, 'utf8').includes('jarvis-safe-router-v1'));
  assert.ok(handlerName, `kein gepatchter Handler in ${distDir}`);
  const source = fs.readFileSync(`${distDir}/${handlerName}`, 'utf8');
  const start = source.indexOf('\tconst runArgs = async (...args) => {');
  const end = source.indexOf('\t\tconst confirmationGrant =', start);
  assert.ok(start >= 0 && end > start);
  const fragment = source.slice(start, end).replace('await import("file:///home/openclaw/.local/share/jarvis-realtime-bridge/scoped-smart-home-router.mjs")', '({ routeSmartHome: route })');
  const h = harness(); let agentCalls = 0, current = turn('Monitor aus');
  const sandbox = {
    setTimeout, jarvisReadBinding: () => ({ room: "Schlafzimmer", deviceId: "device-a", voiceSessionId: "voice-1", sessionKey }), jarvisTurnStorage: voiceTurnStorage, route: h.route, canonicalKey: sessionKey, sessionKey, agentId: 'voice',
    parseRealtimeVoiceAgentConsultArgs: x => x, isAgentEventLifecycleGenerationCurrent: () => true,
    params: {
      getJarvisTurn: () => current,
      waitForJarvisTurn: async (signal, timeoutMs) => {
        if (current) return current;
        await new Promise((resolve, reject) => {
          const timer = setTimeout(resolve, Math.min(timeoutMs, 25));
          signal?.addEventListener('abort', () => { clearTimeout(timer); reject(signal.reason); }, { once: true });
        });
        return current;
      },
      getVoiceSessionId: () => current?.voiceSessionId ?? 'voice-1',
      registerRun: true,
      context: { logGateway: { info() {}, warn() {} } }
    },
    agent: async () => { agentCalls++; await new Promise(r => setTimeout(r, 1)); return { text: voiceTurnStorage.getStore().room }; }
  };
  vm.createContext(sandbox);
  vm.runInContext(fragment + '\n return agent(); }; globalThis.run = runArgs;', sandbox);
  assert.match((await sandbox.run({ question: 'Monitor aus' }, undefined, undefined, undefined, undefined, 'native-delegation')).text, /Alexa/);
  assert.equal(agentCalls, 0); assert.equal(h.sent.length, 1);
  current = turn('Monitor aus und Licht an', 'Arbeitszimmer', 'complex');
  const result = await sandbox.run({ question: current.utterance }, undefined, undefined, undefined, undefined, 'native-delegation');
  assert.equal(result.text, 'Arbeitszimmer'); assert.equal(agentCalls, 1); assert.equal(h.sent.length, 1);
  // Provider delegation may precede the final STT event. A turn that arrives
  // inside the bounded wait must still take the direct route.
  current = undefined;
  setTimeout(() => { current = turn('Licht an', 'Schlafzimmer', 'late'); }, 10);
  const startedAt = Date.now();
  await sandbox.run({ question: 'Licht an' }, undefined, undefined, undefined, undefined, 'native-delegation');
  assert.equal(h.sent.at(-1), 'Schlafzimmer Licht an');
  assert.equal(agentCalls, 1);
  assert.ok(Date.now() - startedAt >= 8, 'direct route must wait for finalized STT');
  // If no final transcript arrives before the bound, the normal agent path
  // remains available with the room binding and nothing is sent directly.
  current = undefined;
  const fallback = await sandbox.run({ question: 'Licht aus' }, undefined, undefined, undefined, undefined, 'native-delegation');
  assert.equal(fallback.text, 'Schlafzimmer', 'agent must still get the room');
  assert.equal(agentCalls, 2);
  assert.equal(h.sent.length, 2, 'no direct send without a finalized turn');
  await assert.rejects(sandbox.run({ question: 'Licht an' }, AbortSignal.abort()));
  assert.equal(agentCalls, 2);
});
