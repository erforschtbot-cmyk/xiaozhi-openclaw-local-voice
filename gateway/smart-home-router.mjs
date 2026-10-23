import fs from 'node:fs';
import { applyRoom, readActiveRoom } from './room-command.mjs';

export const CATALOG = '/home/openclaw/.local/share/jarvis-realtime-bridge/alexa-device-catalog.json';
// Der Helper hinterlegt hier den echten gesprochenen Satz. Beim tool-call ist
// das die einzige verlaessliche Gegenprobe gegen vom Modell gekuerzte Auftraege.
export const SPOKEN_UTTERANCE = '/home/openclaw/.local/share/jarvis-realtime-bridge/last-utterance.json';
const SPOKEN_TTL_MS = 120_000;

// Pfad bei jedem Aufruf aufloesen: Tests und Umgebungen koennen ihn setzen,
// ohne dass ein Modul-Neuladen noetig waere.
export function spokenUtterancePath() {
  return process.env.OPENCLAW_SPOKEN_UTTERANCE_FILE || SPOKEN_UTTERANCE;
}

export function readSpokenUtterance(path = spokenUtterancePath(), now = Date.now()) {
  let data;
  try {
    data = JSON.parse(fs.readFileSync(path, 'utf8'));
  } catch {
    return null;
  }
  const utterance = data?.utterance;
  const ts = Number(data?.ts);
  if (typeof utterance !== 'string' || !utterance.trim()) return null;
  if (!Number.isFinite(ts)) return null;
  if (ts < 1e12) return null; // Helper schreibt Millisekunden
  if (now - ts > SPOKEN_TTL_MS || ts > now + 60_000) return null;
  return utterance.trim();
}
const ENDPOINT = 'http://192.168.178.24:8087/set/alexa2.0.Echo-Devices.G6G2MM12449402WK.Commands.textCommand';
export const normalize = text => String(text).normalize('NFC').toLocaleLowerCase('de').trim().replace(/\s+/g, ' ');
const compound = /\b(?:und|oder|danach|dann|vorher|anschließend|anschliessend|später|spaeter|verzögerung|verzoegerung|wenn|falls|bevor|nachdem|nicht|kein|keine|außer|ausser|aber|erst|gleichzeitig|sekunden?|minuten?|stunden?|morgen|heute|bitte nicht)\b/;
// Limit automatic admission to ordinary low-risk loads. Names alone are not
// sufficient to admit alarms, locks, water, heating, scenes or arbitrary scripts.
const ordinary = /(?:licht|lampe|beleuchtung|strahler|ventilator|lüfter|luefter|monitor|fernseher|steckdose|ambilight)/;
const excluded = /(?:alarm|schloss|tür|tuer|\btor\b|garagentor|wasser|heiz|herd|ofen|kamin|auto|server|computer|\bpc\b|pumpe)/;

export function aliasesFor(name) {
  const n = normalize(name);
  const aliases = [n];
  // Explicit linguistic variants only, no fuzzy matching or guessed room.
  const lamp = /^(?:lampe|licht) (.+)$/.exec(n);
  if (lamp) for (const noun of ['lampe', 'licht']) {
    aliases.push(`${noun} ${lamp[1]}`, `${noun} im ${lamp[1]}`);
    if (lamp[1] === 'küche') aliases.push(`${noun} in der küche`);
  }
  return [...new Set(aliases)];
}

export function buildCatalog(devices, now = Date.now()) {
  const names = new Map();
  for (const d of devices) {
    if (!d.power || typeof d.name !== 'string') continue;
    for (const alias of aliasesFor(d.name)) {
      const set = names.get(alias) ?? new Set(); set.add(d.id); names.set(alias, set);
    }
  }
  const entries = [];
  for (const d of devices) {
    if (!d.power || typeof d.name !== 'string') continue;
    const n = normalize(d.name);
    if (!ordinary.test(n) || excluded.test(n) || compound.test(n) || /[?!,;:\n]/.test(n)) continue;
    const aliases = aliasesFor(n).filter(a => names.get(a)?.size === 1);
    if (aliases.length) entries.push({ id: d.id, name: d.name, aliases });
  }
  return { version: 1, generatedAt: now, discovered: devices.length, entries };
}

export function classify(text, catalog, now = Date.now()) {
  if (typeof text !== 'string' || text.length > 200 || /[?\n\r,;:]/.test(text)) return null;
  if (catalog?.version !== 1 || !Array.isArray(catalog.entries) || !Number.isFinite(catalog.generatedAt)
      || now - catalog.generatedAt > 48 * 3600_000 || catalog.generatedAt > now + 60_000) return null;
  let s = normalize(text).replace(/[.!]$/, '').trim();
  if (compound.test(s)) return null;
  s = s.replace(/^bitte /, '').replace(/ bitte$/, '');
  const m = /^(?:(?:schalte|schalt|mach|mache) )?(?:(?:den|die|das) )?(.+?) (an|ein|aus|einschalten|ausschalten)$/.exec(s);
  if (!m) return null;
  const hits = catalog.entries.filter(d => d.aliases.includes(m[1]));
  if (hits.length !== 1) return null;
  return { deviceId: hits[0].id, deviceName: hits[0].name, action: /^(an|ein|einschalten)$/.test(m[2]) ? 'an' : 'aus', text: text.trim() };
}

// Only accept the native delegation envelope when input is identical to the
// complete, single user transcript. A model-summarized subcommand is NOT enough.
// Der live genutzte gateway-relay-Consult schickt KEINEN XML-Umschlag,
// sondern Klartext (buildRealtimeVoiceAgentConsultPrompt):
//
//   ... Recent voice transcript for context:
//   User: <aeltere Zeile>
//   User: <aktueller Satz>
//
//   User request:
//   <aktueller Satz>
//
// Beide Formen werden akzeptiert. Massgeblich bleibt: der Auftrag muss exakt
// dem vollstaendigen letzten Nutzersatz entsprechen.
function utteranceFromConsultPrompt(prompt) {
  const text = String(prompt);
  const marker = 'User request:';
  const at = text.lastIndexOf(marker);
  if (at === -1) return null;

  const request = text.slice(at + marker.length).trim();
  if (!request) return null;

  const before = text.slice(0, at);
  const userLines = before
    .split('\n')
    .map((line) => line.trim())
    .filter((line) => /^user:\s/i.test(line))
    .map((line) => line.replace(/^user:\s*/i, '').trim())
    .filter(Boolean);

  if (!userLines.length) return request;
  const last = userLines[userLines.length - 1];
  return normalize(request) === normalize(last) ? last : null;
}

export function originalUtterance(prompt) {
  if (typeof prompt !== 'string') return null;
  const m = /^<realtime_delegation>\s*<input>([\s\S]*?)<\/input>\s*<transcript_delta>([\s\S]*?)<\/transcript_delta>\s*<\/realtime_delegation>$/.exec(prompt.trim());
  if (!m) return null;
  const decode = (s) => s.replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&');
  const input = decode(m[1]).trim();
  if (!input) return null;

  // transcript_delta enthaelt den Gespraechsverlauf, nicht nur den aktuellen
  // Satz. Massgeblich ist die LETZTE User-Zeile: nur wenn der Auftrag exakt
  // dieser Zeile entspricht, wird gehandelt. Eine vom Modell umformulierte
  // oder gekuerzte Anfrage faellt damit weiterhin durch.
  const userLines = decode(m[2])
    .split('\n')
    .map((line) => line.trim())
    .filter((line) => line.startsWith('user: '))
    .map((line) => line.slice(6).trim())
    .filter(Boolean);
  if (!userLines.length) return null;

  const last = userLines[userLines.length - 1];
  return normalize(input) === normalize(last) ? last : null;
}

export function originalUtteranceBoth(prompt) {
  const viaXml = originalUtterance(prompt);
  if (viaXml) return viaXml;
  return utteranceFromConsultPrompt(prompt);
}

export function createRouter({ loadCatalog = () => JSON.parse(fs.readFileSync(CATALOG, 'utf8')), send = async (command, signal) => {
  const response = await fetch(`${ENDPOINT}?value=${encodeURIComponent(command)}`, {
    signal: AbortSignal.any([signal ?? new AbortController().signal, AbortSignal.timeout(3000)]),
    redirect: 'error',
  });
  await response.arrayBuffer();
  return response.status;
}, now = Date.now, log = console.info } = {}) {
  const receipts = new Map();
  return async function route({ sessionKey, source, voiceSessionId, question, signal }) {
    // Accept any Jarvis voice session (v5, v6, ...), never a foreign channel.
    // The session key alone does not authorize writes; the remaining checks apply.
    if (typeof sessionKey !== 'string' || !/^agent:voice:xiaozhi-realtime-v\d+$/.test(sessionKey)
        || !voiceSessionId) return null;
    if (source !== 'native-delegation' && source !== 'tool-call') return null;

    // Zwei Quellen, ein Prinzip: gehandelt wird nur, wenn der Auftrag exakt
    // dem gesprochenen Satz entspricht. Das Modell darf weder kuerzen noch
    // umformulieren, sonst koennte ein Teilbefehl durchrutschen.
    const spoken = readSpokenUtterance();
    const raw = typeof question === 'string' ? question.trim() : '';
    if (!raw) return null;

    let utterance = null;
    if (source === 'native-delegation') {
      // Bevorzugt der strukturierte Umschlag. Fehlt er (kommt live vor),
      // wird gegen den gesprochenen Satz geprueft.
      utterance = originalUtteranceBoth(raw);
      if (!utterance) {
        const asked = raw;
        if (!spoken || normalize(asked) !== normalize(spoken)) {
          log(`[jarvis-fast-route] skipped reason=no-envelope asked=${JSON.stringify(asked.slice(0, 80))}`);
          return null;
        }
        utterance = spoken;
      }
    } else {
      const asked = originalUtteranceBoth(raw) ?? raw;
      if (!spoken) {
        log('[jarvis-fast-route] skipped reason=no-spoken-utterance');
        return null;
      }
      if (normalize(spoken) !== normalize(asked)) {
        log(`[jarvis-fast-route] skipped reason=mismatch spoken=${JSON.stringify(spoken)} asked=${JSON.stringify(asked.slice(0, 80))}`);
        return null;
      }
      utterance = spoken;
    }
    if (!utterance) return null;
    let match;
    try { match = classify(utterance, loadCatalog(), now()); } catch { return null; }

    // Zweiter Weg: "Licht an" / "Steckdose an" ohne Raum. Solche Saetze
    // stehen nicht im Geraetekatalog, weil Alexa sie ueber die Gruppe
    // aufloest. Der Raum des sprechenden Geraets wird vorangestellt.
    if (!match) {
      let room = null;
      try { room = readActiveRoom(); } catch { room = null; }
      if (!room) return null;
      const rewritten = applyRoom(utterance, room);
      if (!rewritten.changed) return null;
      match = { deviceId: `group:${room}`, deviceName: `${room} (Gruppe)`, action: 'group', text: rewritten.text };
    }

    signal?.throwIfAborted();
    // One gateway process owns this path. Reserve BEFORE I/O; concurrent calls
    // share the SAME outcome, including uncertain failures. Never fall through
    // to the agent after a write may have happened.
    const key = `${voiceSessionId}:${match.deviceId}:${match.action}:${normalize(match.text)}`;
    for (const [k, v] of receipts) if (now() - v.at > 8000) receipts.delete(k);
    if (receipts.has(key)) return receipts.get(key).promise;
    const started = now();
    const promise = Promise.resolve().then(async () => {
      try {
        signal?.throwIfAborted();
        const status = await send(match.text, signal);
        if (status !== 200) throw new Error(`HTTP ${status}`);
        log(`[jarvis-fast-route] accepted device=${JSON.stringify(match.deviceName)} action=${match.action} http=200 elapsedMs=${now() - started}`);
        return { text: 'An Alexa gesendet.' };
      } catch (error) {
        log(`[jarvis-fast-route] uncertain device=${JSON.stringify(match.deviceName)} elapsedMs=${now() - started} error=${JSON.stringify(String(error))}`);
        return { text: 'Die Übergabe an Alexa konnte ich nicht bestätigen. Ich sende den Befehl nicht erneut.' };
      }
    });
    receipts.set(key, { at: started, promise });
    return promise;
  };
}

export const routeSmartHome = createRouter();
