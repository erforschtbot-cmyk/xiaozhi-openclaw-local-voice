import fs from 'node:fs';
import path from 'node:path';
import { AsyncLocalStorage } from 'node:async_hooks';
import { fileURLToPath } from 'node:url';

const directory = path.join(path.dirname(fileURLToPath(import.meta.url)), 'voice-bindings');
const key = Symbol.for('jarvis.voiceTurn.v1');
export const voiceTurnStorage = globalThis[key] ??= new AsyncLocalStorage();
const validId = id => typeof id === 'string' && /^[a-zA-Z0-9-]{16,100}$/.test(id);

export function bindVoiceSession(voiceSessionId, sessionKey, deviceId, room) {
  if (!validId(voiceSessionId) || !deviceId) throw new Error('Missing voice/device identity');
  fs.mkdirSync(directory, { recursive: true, mode: 0o700 });
  fs.writeFileSync(path.join(directory, `${voiceSessionId}.json`),
    JSON.stringify({ voiceSessionId, sessionKey, deviceId, room: room || null, createdAt: Date.now() }),
    { flag: 'wx', mode: 0o600 });
}

export function readVoiceBinding(voiceSessionId, sessionKey) {
  if (!validId(voiceSessionId)) return null;
  try {
    const value = JSON.parse(fs.readFileSync(path.join(directory, `${voiceSessionId}.json`), 'utf8'));
    return value.voiceSessionId === voiceSessionId && value.sessionKey === sessionKey &&
      typeof value.deviceId === 'string' && Date.now() - value.createdAt < 24 * 3600_000 ? value : null;
  } catch { return null; }
}

export function unbindVoiceSession(voiceSessionId) {
  if (validId(voiceSessionId)) fs.rmSync(path.join(directory, `${voiceSessionId}.json`), { force: true });
}
