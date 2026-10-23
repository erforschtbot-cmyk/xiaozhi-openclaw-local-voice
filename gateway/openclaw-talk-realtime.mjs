#!/usr/bin/env node
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { execFileSync } from "node:child_process";
import { bindVoiceSession, unbindVoiceSession } from "./voice-turn-context.mjs";

const OPENCLAW_NPM_ROOT =
  process.env.OPENCLAW_NPM_ROOT ||
  execFileSync("npm", ["root", "-g"], { encoding: "utf8" }).trim();
const { GatewayClient } = await import(
  pathToFileURL(
    path.join(OPENCLAW_NPM_ROOT, "openclaw/dist/plugin-sdk/gateway-runtime.js"),
  ).href
);

const SESSION_KEY =
  process.env.OPENCLAW_TALK_SESSION_KEY || "agent:voice:voice-assistant";

// Der echte gesprochene Satz wird fuer die Gateway-Weiche hinterlegt. Sie darf
// nur dann direkt schalten, wenn der Tool-Aufruf EXAKT diesem Satz entspricht.
// So kann das Modell keinen Teilbefehl durchreichen.
const SPOKEN_UTTERANCE_FILE =
  process.env.OPENCLAW_SPOKEN_UTTERANCE_FILE ||
  "/home/openclaw/.local/share/jarvis-realtime-bridge/last-utterance.json";

function publishSpokenUtterance(text) {
  try {
    const payload = {
      utterance: text,
      sessionKey: SESSION_KEY,
      ts: Date.now(),
    };
    const tmp = `${SPOKEN_UTTERANCE_FILE}.${process.pid}.tmp`;
    fs.writeFileSync(tmp, JSON.stringify(payload));
    fs.renameSync(tmp, SPOKEN_UTTERANCE_FILE);
  } catch {
    /* Diagnose darf den Sprachpfad nie blockieren */
  }
}
const CONSULT_SESSION_KEY =
  process.env.OPENCLAW_TALK_CONSULT_SESSION_KEY || "";
const MODEL = process.env.OPENCLAW_REALTIME_MODEL || "gpt-realtime-2.1";
const VOICE = process.env.OPENCLAW_REALTIME_VOICE || "cedar";

function emit(type, data = {}) {
  process.stderr.write(`${JSON.stringify({ type, ...data })}\n`);
}

// ---------------------------------------------------------------------------
// Flugschreiber
// ---------------------------------------------------------------------------
// Warum: Ein Ausfall war bisher nur daran erkennbar, dass Ereignisse FEHLTEN.
// Nach dem Turn war dann nicht mehr feststellbar, ob das Audio den Gateway
// erreicht hat, ob OpenAI geantwortet hat oder ob nur die Uebergabe scheiterte.
// Diese Datei schreibt genau das mit — pro Turn eine JSONL-Datei. Sie ist rein
// diagnostisch, blockiert nie und darf den Sprachpfad nicht beeinflussen.
const FLIGHT_DIR =
  process.env.OPENCLAW_FLIGHT_DIR ||
  "/home/openclaw/.local/share/jarvis-realtime-bridge/logs";
const FLIGHT_STARTED_AT = Date.now();
const FLIGHT_PATH = (() => {
  try {
    fs.mkdirSync(FLIGHT_DIR, { recursive: true, mode: 0o700 });
    const stamp = new Date(FLIGHT_STARTED_AT).toISOString().replace(/[:.]/g, "-");
    return path.join(FLIGHT_DIR, `turn-${stamp}-${process.pid}.jsonl`);
  } catch {
    return null;
  }
})();
let flightEvents = 0;
const FLIGHT_MAX_EVENTS = 400;
const flightAudioCounts = new Map();

function record(stage, data = {}) {
  if (!FLIGHT_PATH) return;
  // Bound repetitive audio diagnostics, never lifecycle/error evidence.
  const isAudio = stage === "audio_accepted" || stage === "audio_flushed" ||
    stage === "relay_audio" || (stage === "relay_event" && data.type === "inputAudio");
  if (isAudio) {
    const count = (flightAudioCounts.get(stage) || 0) + 1;
    flightAudioCounts.set(stage, count);
    if (count % 25 !== 1 || flightEvents >= FLIGHT_MAX_EVENTS) return;
    flightEvents += 1;
  }
  try {
    // `data` darf die Zeitmarke nicht ueberschreiben. Genau das passierte:
    // audio_accepted lieferte `at: 0` aus dem Audio-Zeitstempel und ersetzte
    // den ISO-Zeitpunkt. Diagnosefelder bekommen deshalb das Praefix `d`.
    const extra = {};
    for (const [key, value] of Object.entries(data)) extra[`d${key}`] = value;
    const line = JSON.stringify({
      at: new Date().toISOString(),
      sinceStartMs: Date.now() - FLIGHT_STARTED_AT,
      stage,
      sessionId: sessionId ?? null,
      ...extra,
    });
    fs.appendFileSync(FLIGHT_PATH, line + "\n");
  } catch {
    /* Diagnose darf den Sprachpfad nie blockieren */
  }
}

function gatewayToken() {
  const secretsFile =
    process.env.OPENCLAW_SECRETS_FILE ||
    path.join(os.homedir(), ".openclaw", "secrets.json");
  const secrets = JSON.parse(
    fs.readFileSync(secretsFile, "utf8"),
  );
  const token = secrets.gateway?.authToken;
  if (!token) throw new Error("OpenClaw gateway auth token missing");
  return token;
}

function pcm16ToMonoSamples(input, inputChannels) {
  const frameCount = Math.floor(input.length / (2 * inputChannels));
  const samples = new Int16Array(frameCount);
  for (let frame = 0; frame < frameCount; frame += 1) {
    let sum = 0;
    for (let channel = 0; channel < inputChannels; channel += 1) {
      sum += input.readInt16LE((frame * inputChannels + channel) * 2);
    }
    samples[frame] = Math.max(
      -32768,
      Math.min(32767, Math.round(sum / inputChannels)),
    );
  }
  return samples;
}

function resampleLinear(samples, fromRate, toRate) {
  if (fromRate === toRate) return samples;
  const outLength = Math.max(1, Math.round((samples.length * toRate) / fromRate));
  const output = new Int16Array(outLength);
  const ratio = fromRate / toRate;
  for (let index = 0; index < outLength; index += 1) {
    const position = index * ratio;
    const low = Math.floor(position);
    const high = Math.min(samples.length - 1, low + 1);
    const fraction = position - low;
    output[index] = Math.round(
      samples[low] * (1 - fraction) + samples[high] * fraction,
    );
  }
  return output;
}

function toPcm24k(input, rate, channels) {
  const resampled = resampleLinear(
    pcm16ToMonoSamples(input, channels),
    rate,
    24000,
  );
  const output = Buffer.alloc(resampled.length * 2);
  for (let index = 0; index < resampled.length; index += 1) {
    output.writeInt16LE(resampled[index], index * 2);
  }
  return output;
}

function finalMessageText(message) {
  if (typeof message === "string") return message;
  const content = message?.content;
  if (!Array.isArray(content)) return "";
  return content
    .map((part) => (part?.type === "text" ? part.text ?? "" : ""))
    .join("");
}

const FASTPATH_DISABLE_FLAG =
  "/home/openclaw/.local/share/jarvis-realtime-bridge/disable-smart-home-fastpath";

const PHOTO_UTTERANCE =
  /\b(?:mach|mache|nimm|nehme|schiess|schieße|knips|schick|schicke)\b[^.]{0,40}\b(?:foto|bild)\b|\b(?:foto|bild)\b[^.]{0,40}\b(?:mach|machen|aufnehmen|schiessen|schießen|schicken|schick)\b|\bwas\s+(?:siehst|erkennst)\s+du\b|\bschau\s+(?:mal|dir)\b|\bsieh\s+dir\s+(?:das|mal)\s+an\b/;

function isPhotoRequest(text) {
  return PHOTO_UTTERANCE.test(String(text ?? "").toLowerCase().replace(/\s+/g, " "));
}

function isSmartHomeFastPath(text) {
  // Muss dasselbe Flag wie server.py beachten: ist der Software-Direktpfad
  // abgeschaltet, darf der Helper den KI-Consult NICHT unterdruecken, sonst
  // schaltet niemand.
  try {
    if (fs.existsSync(FASTPATH_DISABLE_FLAG)) return false;
  } catch {
    /* ignore */
  }
  const normalized = String(text ?? "")
    .toLowerCase()
    .replaceAll("%", " prozent ")
    .trim()
    .replace(/\s+/g, " ");
  const target = /[a-z\u00e4\u00f6\u00fc\u00df-]*(?:monitor|licht|lampe|steckdose|jalousie|rollladen|heizung|thermostat|ventilator)\b/;
  const action = /\b(?:an|aus|ein|einschalten|ausschalten|hoch|runter|öffnen|oeffnen|schließen|schliessen|dimmen|stelle|stell|mach|schalte)\b/;
  const question = /\b(?:ob|warum|wieso|weshalb)\b/;
  const stateQuestion = /^(?:ist|sind|war|waren|steht|stehen|läuft|laeuft|geht)\b/;
  return target.test(normalized)
    && action.test(normalized)
    && !question.test(normalized)
    && !stateQuestion.test(normalized);
}

const [rateText, widthText, channelsText] = process.argv.slice(2);
const rate = Number(rateText);
const width = Number(widthText);
const channels = Number(channelsText);
if (!rate || width !== 2 || !channels) {
  throw new Error("Usage: openclaw-talk-realtime.mjs RATE WIDTH CHANNELS");
}

let readyResolve;
let readyReject;
const ready = new Promise((resolve, reject) => {
  readyResolve = resolve;
  readyReject = reject;
});
let sessionId;
let relayClosedBeforeTranscript = false;
let relayRecoveries = 0;
let userFinal = false;
let userFinalAt = 0;
let selfConsultStarted = false;
let assistantFinal = false;
let audioDone = false;
let assistantText = "";
let activeConsults = 0;
// Hat der Provider in diesem Turn einen Werkzeugaufruf ausgeloest?
let sawToolCall = false;
// Laeuft bereits ein Ersatz-Consult fuer eine blosse Ankuendigung?
let fallbackConsult = null;

// Reine Ankuendigung ohne Inhalt. Belegt 22.10.2026: "Ich schaue mir das an."
// wurde als fertige Antwort gewertet, der Turn endete, und der Nutzer hoerte
// nie ein Ergebnis. Diese Muster sind bewusst nur Ankuendigungen — eine echte
// Antwort ("Es ist 15 Uhr") passt auf keines davon.
const INTERIM_PATTERNS = [
  /^(?:einen\s+)?moment(?:\s+bitte)?[.!]?$/i,
  /^(?:einen\s+)?augenblick(?:\s+bitte)?[.!]?$/i,
  /^ich\s+(?:pr(?:ue|ü)fe|schaue|sehe|gucke)\b.{0,40}$/i,
  /^ich\s+(?:k(?:ue|ü)mmere|k\u00fcmmere)\s+mich\b.{0,40}$/i,
  /^(?:let me|i'?ll|i will)\s+(?:check|look|see)\b.{0,40}$/i,
  /^(?:one\s+)?moment(?:\s+please)?[.!]?$/i,
];

function looksLikeInterim(text) {
  const normalized = String(text ?? "").trim().replace(/\s+/g, " ");
  if (!normalized) return false;
  return INTERIM_PATTERNS.some((re) => re.test(normalized));
}

// Letzter vom Provider gesprochener Text dieses Turns.
function lastAssistantText() {
  return assistantText.trim();
}
let responseCompletedAt = 0;
let audioFrameCount = 0;
let audioByteCount = 0;
let lastAudioAt = 0;
let assistantFinalAt = 0;
let loggedAudioAfterFinal = false;
let lastUserTranscript = "";
let assistantAudioGateUntil = 0;
// Lebensbeweis des Providers: erst ein echtes talk.event zaehlt.
let providerAlive = false;
let readyEmitted = false;
let sessionCreatedAt = 0;
let audioEventCount = 0;
let audioGateOpen = false;
let stdoutWriteTail = Promise.resolve();
const pendingRuns = new Map();

const client = new GatewayClient({
  url: "wss://127.0.0.1:18789",
  token: gatewayToken(),
  clientName: "cli",
  clientDisplayName: "Home Assistant Realtime Voice Bridge",
  clientVersion: "1.0.0",
  platform: "linux",
  mode: "cli",
  role: "operator",
  scopes: ["operator.read", "operator.write"],
  onHelloOk: readyResolve,
  onConnectError: readyReject,
  onEvent: async (frame) => {
    if (frame.event === "chat") {
      const payload = frame.payload;
      const pending = pendingRuns.get(payload?.runId);
      if (!pending) return;
      if (payload.state === "final") {
        pendingRuns.delete(payload.runId);
        pending.resolve(finalMessageText(payload.message) || "Erledigt.");
      } else if (payload.state === "error" || payload.state === "aborted") {
        pendingRuns.delete(payload.runId);
        pending.reject(new Error(payload.errorMessage || "Agent run failed"));
      }
      return;
    }
    if (frame.event !== "talk.event") return;
    const event = frame.payload;
    if (!event || event.relaySessionId !== sessionId) return;
    // WICHTIG: Nur das echte `ready`-Ereignis beweist, dass die OpenAI-Sitzung
    // arbeitet. `inputAudio` ist das Echo unseres eigenen Audios und kommt
    // schon, waehrend der Provider noch gar nicht angeschlossen ist — genau
    // daran ist die erste Fassung gescheitert (Flugschreiber 21.10.2026:
    // Audio 1066-1518 ms, echtes ready erst 3127 ms, alles Audio verworfen).
    if (event.type === "ready" && !providerAlive) {
      providerAlive = true;
      record("provider_ready", {
        afterSessionMs: Date.now() - sessionCreatedAt,
        bufferedAudioMs: bufferedInputMs,
      });
      audioGateOpen = true;
      void flushBufferedAudio();
      maybeEmitReady();
    }
    // Ereignisarten mitschreiben. Audio-Frames werden gesampelt, sonst
    // ueberfluten sie den Speicher und verdraengen die Zustandszeilen, die bei
    // einem Ausfall tatsaechlich gebraucht werden.
    if (event.type === "audio") {
      audioEventCount += 1;
      if (audioEventCount % 25 === 1) {
        record("relay_audio", { count: audioEventCount });
      }
    } else {
      record("relay_event", {
        type: event.type, role: event.role ?? null,
        ...(event.type === "error" || event.type === "close" ? {
          message: String(event.message ?? event.error?.message ?? "").slice(0, 300),
          reason: String(event.reason ?? "").slice(0, 200),
        } : {}),
      });
    }
    if (event.type === "close") {
      if (!userFinal) {
        relayClosedBeforeTranscript = true;
        emit("relay_recovering", {
          reason: event.reason || "provider_closed",
          sessionId,
        });
      } else {
        emit("error", {
          message: `OpenAI realtime relay closed after transcript: ${event.reason || "unknown"}`,
        });
      }
      return;
    }
    if (event.type === "audio" && event.audioBase64) {
      const audio = Buffer.from(event.audioBase64, "base64");
      audioFrameCount += 1;
      audioByteCount += audio.length;
      lastAudioAt = Date.now();
      if (assistantFinalAt > 0 && !loggedAudioAfterFinal) {
        loggedAudioAfterFinal = true;
        emit("audio_after_assistant_done", {
          delayMs: lastAudioAt - assistantFinalAt,
          audioFrameCount,
          audioByteCount,
        });
      }
      stdoutWriteTail = stdoutWriteTail.then(
        async () => {
          // The bridge classifies finalized transcripts in a separate process.
          // Hold the first assistant audio very briefly so a direct smart-home
          // command can terminate this helper before fragments of the generic
          // "I'll check" acknowledgement reach the speaker. This does not
          // delay transcript delivery or the direct Alexa HTTP request.
          const gateDelay = assistantAudioGateUntil - Date.now();
          if (gateDelay > 0) {
            await new Promise((resolve) => setTimeout(resolve, gateDelay));
          }
          await new Promise((resolve, reject) => {
          process.stdout.write(audio, (error) => {
            if (error) reject(error);
            else resolve();
          });
          });
        },
      );
      return;
    }
    if (event.type === "audioDone") {
      audioDone = true;
      if (assistantFinal) responseCompletedAt = Date.now();
      void stdoutWriteTail.then(() => {
        emit("audio_done", {
          audioFrameCount,
          audioByteCount,
          msSinceLastAudio: lastAudioAt ? Date.now() - lastAudioAt : null,
        });
      }).catch((error) => emit("error", { message: String(error) }));
      return;
    }
    if (event.type === "transcript") {
      if (event.role === "user" && event.final && event.text?.trim()) {
        lastUserTranscript = event.text.trim();
        // Gateway owns the authoritative transcript and per-turn identity.
        assistantAudioGateUntil = Date.now() + 200;
        userFinal = true;
        assistantFinal = false;
        audioDone = false;
        assistantText = "";
        audioFrameCount = 0;
        audioByteCount = 0;
        lastAudioAt = 0;
        responseCompletedAt = 0;
        assistantFinalAt = 0;
        loggedAudioAfterFinal = false;
        emit("user_transcript", { text: event.text.trim() });
      } else if (event.role === "assistant" && event.text) {
        let delta = event.text;
        if (event.text.startsWith(assistantText)) {
          delta = event.text.slice(assistantText.length);
          assistantText = event.text;
        } else if (!event.final) {
          assistantText += event.text;
        }
        if (delta) emit("assistant_delta", { text: delta });
        if (event.final) {
          assistantFinal = true;
          assistantFinalAt = Date.now();
          loggedAudioAfterFinal = false;
          if (audioDone) responseCompletedAt = Date.now();
          emit("assistant_done", {
            text: event.text,
            audioFrameCount,
            audioByteCount,
            msSinceLastAudio: lastAudioAt ? assistantFinalAt - lastAudioAt : null,
          });
        }
      }
      return;
    }
    if (event.type === "toolCall" && event.callId && event.name) {
      sawToolCall = true;
      emit("tool_call", {
        callId: event.callId,
        name: event.name,
        forced: event.forced === true,
        consultSessionKey: CONSULT_SESSION_KEY || null,
      });
      activeConsults += 1;
      assistantFinal = false;
      audioDone = false;
      assistantText = "";
      responseCompletedAt = 0;
      assistantFinalAt = 0;
      loggedAudioAfterFinal = false;
      // Do not await the consult inside the gateway event callback. GatewayClient
      // dispatches events serially, so awaiting here would prevent the later
      // chat events from reaching the pendingRuns resolver.
      void (async () => {
        try {
          if (event.name === "openclaw_agent_consult") {
            if (isPhotoRequest(lastUserTranscript)) {
              // server.py loest die Kamera direkt aus und laesst den Agenten
              // das Bild auswerten. Ein zweiter Agentenlauf hier kennt das
              // Foto nicht und erfindet eine Fehlermeldung ("Bild kam schwarz
              // an") — belegt am 20.10.2026.
              await client.request("talk.session.submitToolResult", {
                sessionId,
                callId: event.callId,
                result: {
                  result: "Das Foto wurde bereits aufgenommen und ausgewertet. Führe kein Werkzeug aus und beschreibe kein Bild.",
                },
              });
              emit("tool_call_suppressed", {
                callId: event.callId,
                name: event.name,
                reason: "photo_request",
              });
              return;
            }
            if (isSmartHomeFastPath(lastUserTranscript)) {
              // server.py executes this command directly as soon as the final
              // transcript arrives. Never start a second agent run for the
              // same utterance, even if the provider emitted its tool call in
              // the few milliseconds before the helper is terminated.
              await client.request("talk.session.submitToolResult", {
                sessionId,
                callId: event.callId,
                result: {
                  result: "Der Smart-Home-Befehl wurde bereits sofort und erfolgreich ausgeführt. Führe kein Werkzeug aus. Bestätige den Erfolg jetzt kurz auf Deutsch.",
                },
              });
              emit("tool_call_suppressed", {
                callId: event.callId,
                name: event.name,
                reason: "smart_home_fast_path",
              });
              return;
            }
            let started;
            if (CONSULT_SESSION_KEY) {
              const args = event.args ?? {};
              const question = String(
                args.question ?? args.prompt ?? args.query ?? args.task ?? "",
              ).trim();
              if (!question) throw new Error("agent consult question missing");
              const context = String(args.context ?? "").trim();
              const message = [
                "Beantworte die folgende Sprachfrage auf Deutsch, kurz und direkt.",
                "Gib ausschließlich die fertige Antwort aus: keine Warteansage, keine Zwischenmeldung und keine Rückfrage über eine UI.",
                "Behandle nur die aktuelle Sprachfrage. Ignoriere alte ausstehende Aktionen oder Bestätigungen aus anderen Gesprächsständen.",
                "Wenn die aktuelle Frage unklar oder unvollständig ist, antworte genau: Das habe ich nicht verstanden. Bitte wiederhole die Frage.",
                "Fordere niemals pauschal zu Ja oder Nein auf, ohne die konkrete zu bestätigende Aktion im selben Satz zu nennen.",
                `Frage: ${question}`,
                context ? `Kontext: ${context}` : "",
              ].filter(Boolean).join("\n");
              started = await client.request("chat.send", {
                sessionKey: CONSULT_SESSION_KEY,
                message,
                idempotencyKey: `xiaozhi-consult-${event.callId}-${Date.now()}`,
                suppressCommandInterpretation: true,
              });
            } else {
              started = await client.request("talk.client.toolCall", {
                sessionKey: SESSION_KEY,
                callId: event.callId,
                name: event.name,
                args: event.args ?? {},
                relaySessionId: sessionId,
              });
            }
            const result = await new Promise((resolve, reject) => {
              pendingRuns.set(started.runId ?? started.idempotencyKey, {
                resolve,
                reject,
              });
            });
            await client.request("talk.session.submitToolResult", {
              sessionId,
              callId: event.callId,
              result: { result },
            });
            emit("tool_result", {
              callId: event.callId,
              name: event.name,
              result,
            });
          } else {
            await client.request("talk.session.submitToolResult", {
              sessionId,
              callId: event.callId,
              result: { error: `Unsupported tool ${event.name}` },
            });
          }
        } catch (error) {
          emit("error", { message: String(error) });
        } finally {
          activeConsults = Math.max(0, activeConsults - 1);
        }
      })();
      return;
    }
  },
});

client.start();
await Promise.race([
  ready,
  new Promise((_, reject) =>
    setTimeout(() => reject(new Error("Gateway connect timeout")), 10000),
  ),
]);

async function createRelaySession() {
  const previousSessionId = sessionId;
  const session = await client.request("talk.session.create", {
    sessionKey: SESSION_KEY,
    provider: "openai",
    model: MODEL,
    voice: VOICE,
    mode: "realtime",
    transport: "gateway-relay",
    brain: "agent-consult",
    vadThreshold: 0.2,
    // Short home-control phrases should finalize quickly. 700 ms still tolerates
    // normal pauses but removes the former three-second delay after speech.
    silenceDurationMs: 700,
    prefixPaddingMs: 200,
    reasoningEffort: "minimal",
  });
  if (previousSessionId) unbindVoiceSession(previousSessionId);
  sessionId = session.sessionId;
  bindVoiceSession(sessionId, SESSION_KEY, process.env.OPENCLAW_VOICE_DEVICE_ID,
    process.env.OPENCLAW_VOICE_ROOM);
  relayClosedBeforeTranscript = false;
  sessionCreatedAt = Date.now();
  providerAlive = false;
  readyEmitted = false;
  audioGateOpen = false;
  record("session_created", {
    newSessionId: session.sessionId,
    previousSessionId: previousSessionId || null,
    recovery: relayRecoveries,
    model: MODEL,
    voice: VOICE,
  });
  return session;
}

// Bereit erst nach dem ersten echten Provider-Ereignis melden.
function maybeEmitReady() {
  if (readyEmitted || !providerAlive) return;
  readyEmitted = true;
  record("ready_emitted", {
    afterSessionMs: Date.now() - sessionCreatedAt,
    recovery: relayRecoveries,
  });
  emit("ready", {
    sessionId,
    consultSessionKey: CONSULT_SESSION_KEY || null,
  });
}

const session = await createRelaySession();
process.once("exit", () => sessionId && unbindVoiceSession(sessionId));
// Kein sofortiges "ready": die Bridge wuerde sonst Gruen zeigen, obwohl
// hinter der Gateway-ID noch keine OpenAI-Sitzung arbeitet. Das Signal kommt
// aus maybeEmitReady(), sobald der Provider das erste Ereignis schickt.
const PROVIDER_LIVENESS_TIMEOUT_MS = 4000;

// Schlusszeile auch dann schreiben, wenn der Turn scheitert oder der Prozess
// hart beendet wird. Sonst endet genau der interessante Fall ohne Befund.
let turnFinished = false;
function recordTurnFinished(outcome) {
  if (turnFinished) return;
  turnFinished = true;
  record("turn_finished", {
    outcome,
    userFinal,
    assistantFinal,
    audioDone,
    audioByteCount,
    audioEvents: audioEventCount,
    transcript: lastUserTranscript.slice(0, 160),
    providerAlive,
    recoveries: relayRecoveries,
    durationMs: Date.now() - FLIGHT_STARTED_AT,
  });
  if (FLIGHT_PATH) process.stderr.write(`[flight] ${FLIGHT_PATH}\n`);
}

process.on("uncaughtException", (error) => {
  record("turn_crashed", { message: String(error?.message || error).slice(0, 300) });
  recordTurnFinished("crash");
  // Sitzung ZUERST schliessen: ein Absturz ohne close liess die
  // OpenAI-Realtime-Sitzung weiterlaufen und verbrauchte Kontingent
  // (belegt 21.10.2026: 560 s Leerlauf).
  client.request("talk.session.close", { sessionId }).catch(() => {}).finally(() => {
    process.exit(1);
  });
});
process.on("unhandledRejection", (error) => {
  record("turn_crashed", { message: String(error?.message || error).slice(0, 300) });
  recordTurnFinished("rejection");
  client.request("talk.session.close", { sessionId }).catch(() => {}).finally(() => {
    process.exit(1);
  });
});
process.on("exit", (code) => {
  if (!turnFinished) {
    record("turn_exited", { code });
    recordTurnFinished("exit");
  }
});

let timestamp = 0;
// Standby stops this child, not the device transport. Close its relay first.
let stopping = false;
async function stopOwnedSession() {
  if (stopping) return;
  stopping = true;
  audioGateOpen = false;
  process.stdin.pause();
  try {
    await client.request("talk.session.close", { sessionId }, { timeoutMs: 2500 });
    record("provider_close_confirmed");
  } catch (error) {
    record("provider_close_failed", { message: String(error.message).slice(0, 200) });
  }
  await client.stopAndWait({ timeoutMs: 1000 }).catch(() => {});
  recordTurnFinished("stopped");
  process.exit(0);
}
process.once("SIGTERM", stopOwnedSession);
process.once("SIGINT", stopOwnedSession);
const bufferedInput = [];
let bufferedInputMs = 0;
let sentIndex = 0;
const MAX_REPLAY_AUDIO_MS = 30000;
let flushing = null;

// Sendet alles, was waehrend des Provider-Starts gepuffert wurde.
// Idempotent und serialisiert, damit Loop und ready-Ereignis nicht doppelt senden.
async function flushBufferedAudio() {
  if (!audioGateOpen) return;
  if (flushing) return flushing;
  flushing = (async () => {
    while (sentIndex < bufferedInput.length) {
      const entry = bufferedInput[sentIndex];
      await appendRelayAudio(entry.audioBase64, entry.at);
      sentIndex += 1;
      record("audio_flushed", { at: entry.at, index: sentIndex - 1 });
    }
  })().finally(() => {
    flushing = null;
  });
  return flushing;
}

async function appendRelayAudio(audioBase64, at) {
  try {
    await client.request("talk.session.appendAudio", {
      sessionId,
      audioBase64,
      timestamp: at,
    }, { timeoutMs: 8000 });
    record("audio_accepted", { at });
  } catch (error) {
    record("audio_rejected", {
      at,
      message: String(error?.message || error).slice(0, 200),
    });
    throw error;
  }
}

async function recoverRelayAndReplay(reason) {
  if (userFinal) throw new Error(`Realtime relay failed after transcript: ${reason}`);
  if (relayRecoveries >= 2) {
    throw new Error(`Realtime relay recovery exhausted: ${reason}`);
  }
  relayRecoveries += 1;
  record("recovery_started", {
    reason: String(reason).slice(0, 200),
    attempt: relayRecoveries,
    bufferedAudioMs: bufferedInputMs,
  });
  emit("relay_recovering", {
    reason,
    attempt: relayRecoveries,
    bufferedAudioMs: bufferedInputMs,
  });
  // Do not abandon a possibly still-starting provider session on recovery.
  // If its close cannot be confirmed, fail rather than open another session.
  audioGateOpen = false;
  if (flushing) await flushing.catch(() => {});
  try {
    await client.request("talk.session.close", { sessionId }, { timeoutMs: 3000 });
    record("recovery_previous_closed", { previousSessionId: sessionId });
  } catch (error) {
    const message = String(error?.message || error).slice(0, 200);
    // "Unknown Talk session" BEWEIST, dass die Sitzung schon weg ist: der Relay
    // hat sich selbst mit error geschlossen, bevor wir hier ankamen. Weiter ist
    // sicher und kann nicht doppelt abrechnen. Nur ein unbestaetigter Close
    // (Timeout/Netzfehler) muss den Versuch stoppen. Belegt am 22.10.2026, 18:06:
    // hier flog naemlich genau diese Exception und riss den ganzen Helfer mit,
    // obwohl die Sitzung laengst beendet war.
    if (!/Unknown Talk session|not found|no longer|closed/i.test(message)) {
      record("recovery_close_unconfirmed", { message });
      throw error;
    }
    record("recovery_previous_already_closed", { message });
  }
  // Neue Sitzung anlegen. NICHT sofort nachspielen: die neue Sitzung ist erst
  // bereit, wenn ihr `ready` eintrifft. Genau dieses Sofort-Senden war der
  // Fehler vom 21.10.2026 — Audio ging 1,6 s vor dem Provider-Start raus und
  // wurde verworfen. Das Nachspielen uebernimmt flushBufferedAudio().
  sentIndex = 0;
  // Existing replay entries retain their timestamps. New live audio must
  // continue after those entries, not restart at zero behind them.
  await createRelaySession();
  record("recovery_finished", {
    attempt: relayRecoveries,
    bufferedAudioMs: bufferedInputMs,
  });
  emit("relay_recovered", {
    attempt: relayRecoveries,
    sessionId,
    bufferedAudioMs: bufferedInputMs,
  });
}

for await (const chunk of process.stdin) {
  const pcm = toPcm24k(Buffer.from(chunk), rate, channels);
  if (!pcm.length) continue;
  const durationMs = Math.round((pcm.length / 2 / 24000) * 1000);
  const audioBase64 = pcm.toString("base64");
  bufferedInput.push({ audioBase64, durationMs, at: timestamp });
  timestamp += durationMs;
  bufferedInputMs += durationMs;
  while (
    sentIndex < bufferedInput.length &&
    bufferedInputMs > MAX_REPLAY_AUDIO_MS
  ) {
    bufferedInputMs -= bufferedInput.shift().durationMs;
    sentIndex = Math.max(0, sentIndex - 1);
  }
  // Vor dem Provider-Start wird NICHTS gesendet. Der Gateway nimmt Audio auch
  // ohne laufende OpenAI-Sitzung an — es geht dann verloren und der Nutzer
  // spricht ins Leere (belegt im Flugschreiber: Audio 1066-1518 ms, ready erst
  // 3127 ms). Deshalb: puffern, bis das echte ready kommt.
  if (!audioGateOpen) {
    if (!userFinal && Date.now() - sessionCreatedAt >= PROVIDER_LIVENESS_TIMEOUT_MS) {
      await recoverRelayAndReplay("no_provider_event");
    }
    continue;
  }
  if (relayClosedBeforeTranscript) {
    await recoverRelayAndReplay("provider_closed");
    continue;
  }
  try {
    await flushBufferedAudio();
  } catch (error) {
    const message = String(error?.message || error);
    if (
      !userFinal &&
      /Unknown Talk session|realtime_unavailable|session.*(?:closed|unavailable)/i.test(message)
    ) {
      await recoverRelayAndReplay(message);
      continue;
    }
    throw error;
  }
}

// Vor dem Abschluss sicherstellen, dass das gepufferte Audio draussen ist.
if (!audioGateOpen) {
  const waitUntil = Date.now() + PROVIDER_LIVENESS_TIMEOUT_MS;
  while (!audioGateOpen && Date.now() < waitUntil) {
    await new Promise((resolve) => setTimeout(resolve, 25));
  }
}
await flushBufferedAudio().catch((error) => {
  record("flush_failed", { message: String(error?.message || error).slice(0, 200) });
});

// Ensure provider VAD sees a clean end even when HA cuts immediately after speech.
const silence = Buffer.alloc(24000 * 2 * 0.45);
await client.request("talk.session.appendAudio", {
  sessionId,
  audioBase64: silence.toString("base64"),
  timestamp,
}, { timeoutMs: 8000 });

const deadline = Date.now() + 120000;
while (
  Date.now() < deadline &&
  !process.stdout.destroyed
) {
  // A provider can finish a spoken acknowledgement immediately before
  // emitting its tool call. Keep the relay alive briefly so that consult and
  // its follow-up speech remain part of the same realtime turn.
  if (
    userFinal &&
    assistantFinal &&
    audioDone &&
    activeConsults === 0 &&
    pendingRuns.size === 0 &&
    responseCompletedAt > 0 &&
    Date.now() - responseCompletedAt >= 1500
  ) {
    break;
  }
  await new Promise((resolve) => setTimeout(resolve, 25));
}

await client.request("talk.session.close", { sessionId }).catch(() => {});
await client.stopAndWait({ timeoutMs: 3000 });
recordTurnFinished("completed");
if (FLIGHT_PATH) process.stderr.write(`[flight] ${FLIGHT_PATH}\n`);
if (!userFinal) throw new Error("Realtime Talk returned no final user transcript");
