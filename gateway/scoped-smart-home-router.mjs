import fs from 'node:fs';
import { classify, CATALOG, normalize } from './smart-home-router.mjs';
import { applyRoom } from './room-command.mjs';

const ENDPOINT = 'http://192.168.178.24:8087/set/alexa2.0.Echo-Devices.G6G2MM12449402WK.Commands.textCommand';

// Extract only the requested action. The independently observed, finalized
// transcript remains authoritative; prompt-provided history is never trusted.
export function requestedText(question) {
  if (typeof question !== 'string') return '';
  const text = question.trim();
  if (text.startsWith('<realtime_delegation>')) {
    const m = /^<realtime_delegation>\s*<input>([^<>]*)<\/input>/.exec(text);
    return m ? m[1].replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&').trim() : '';
  }
  const marker = '\nUser request:\n';
  if (text.includes(marker)) return text.slice(text.lastIndexOf(marker) + marker.length).trim();
  return text;
}

export function createScopedRouter({
  loadCatalog = () => JSON.parse(fs.readFileSync(CATALOG, 'utf8')),
  now = Date.now, log = console.info,
  send = async (command, signal) => {
    const response = await fetch(`${ENDPOINT}?value=${encodeURIComponent(command)}`, {
      signal: AbortSignal.any([signal ?? new AbortController().signal, AbortSignal.timeout(3000)]), redirect: 'error'
    });
    await response.body?.cancel();
    return response.status;
  }
} = {}) {
  const receipts = new Map();
  return async function route({ sessionKey, source, voiceSessionId, question, signal, turn }) {
    const skip = reason => { log(`[jarvis-fast-route] skipped reason=${reason} voice=${voiceSessionId} turn=${turn?.turnId ?? 'missing'}`); return null; };
    if (!/^agent:voice:xiaozhi-realtime-v\d+$/.test(sessionKey ?? '')) return skip('foreign-session');
    if (!['native-delegation', 'tool-call'].includes(source)) return skip('unsupported-source');
    if (!turn?.turnId || !turn.deviceId || turn.voiceSessionId !== voiceSessionId || turn.sessionKey !== sessionKey) return skip('unbound-turn');
    if (!Number.isFinite(turn.observedAt) || now() - turn.observedAt > 120000 || turn.observedAt > now()) return skip('stale-turn');
    if (typeof turn.utterance !== 'string' || normalize(requestedText(question)) !== normalize(turn.utterance)) return skip('transcript-mismatch');
    // A speech turn is either direct or agent-owned for its entire lifetime.
    // Duplicate delegation of an already-decided turn cannot change that choice.
    for (const [key, receipt] of receipts) if (now() - receipt.at > 30 * 60_000) receipts.delete(key);
    const key = `${voiceSessionId}:${turn.turnId}`;
    if (receipts.has(key)) return receipts.get(key).result;
    let catalog;
    try { catalog = loadCatalog(); } catch { return skip('catalog-unavailable'); }
    if (catalog?.version !== 1 || !Array.isArray(catalog.entries) || !Number.isFinite(catalog.generatedAt) || now() - catalog.generatedAt > 48 * 3600_000 || catalog.generatedAt > now() + 60000) return skip('catalog-stale');
    let match = classify(turn.utterance, catalog, now());
    if (!match && typeof turn.room === 'string') {
      const rewritten = applyRoom(turn.utterance, turn.room);
      if (rewritten.changed) match = { text: rewritten.text, deviceName: `${turn.room} (Gruppe)`, action: 'group' };
    }
    if (!match) { receipts.set(key, { at: now(), result: null }); return skip('not-single-known-command'); }
    signal?.throwIfAborted();
    const started = now();
    const result = Promise.resolve().then(async () => {
      try {
        signal?.throwIfAborted();
        const status = await send(match.text, signal);
        if (status !== 200) throw new Error(`HTTP ${status}`);
        log(`[jarvis-fast-route] accepted device=${JSON.stringify(match.deviceName)} action=${match.action} http=200 elapsedMs=${now() - started} turn=${turn.turnId}`);
        return { text: `An Alexa gesendet: ${match.text}.` };
      } catch (error) {
        log(`[jarvis-fast-route] uncertain turn=${turn.turnId} error=${JSON.stringify(String(error))}`);
        return { text: 'Die Übergabe an Alexa konnte ich nicht bestätigen. Ich sende den Befehl nicht erneut.' };
      }
    });
    receipts.set(key, { at: now(), result });
    return result;
  };
}

export const routeSmartHome = createScopedRouter();
