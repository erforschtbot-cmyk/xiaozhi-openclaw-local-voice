#!/usr/bin/env node
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { execFileSync } from "node:child_process";

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
const CONSULT_SESSION_KEY =
  process.env.OPENCLAW_TALK_CONSULT_SESSION_KEY || "";
const MODEL = process.env.OPENCLAW_REALTIME_MODEL || "gpt-realtime-2.1";
const VOICE = process.env.OPENCLAW_REALTIME_VOICE || "cedar";

function emit(type, data = {}) {
  process.stderr.write(`${JSON.stringify({ type, ...data })}\n`);
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
let userFinal = false;
let assistantFinal = false;
let audioDone = false;
let assistantText = "";
let activeConsults = 0;
let responseCompletedAt = 0;
let audioFrameCount = 0;
let audioByteCount = 0;
let lastAudioAt = 0;
let assistantFinalAt = 0;
let loggedAudioAfterFinal = false;
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
        () => new Promise((resolve, reject) => {
          process.stdout.write(audio, (error) => {
            if (error) reject(error);
            else resolve();
          });
        }),
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

const session = await client.request("talk.session.create", {
  sessionKey: SESSION_KEY,
  provider: "openai",
  model: MODEL,
  voice: VOICE,
  mode: "realtime",
  transport: "gateway-relay",
  brain: "agent-consult",
  vadThreshold: 0.2,
  silenceDurationMs: 3000,
  prefixPaddingMs: 200,
  reasoningEffort: "minimal",
});
sessionId = session.sessionId;
emit("ready", {
  sessionId,
  consultSessionKey: CONSULT_SESSION_KEY || null,
});

let timestamp = 0;
for await (const chunk of process.stdin) {
  const pcm = toPcm24k(Buffer.from(chunk), rate, channels);
  if (!pcm.length) continue;
  await client.request("talk.session.appendAudio", {
    sessionId,
    audioBase64: pcm.toString("base64"),
    timestamp,
  });
  timestamp += Math.round((pcm.length / 2 / 24000) * 1000);
}

// Ensure provider VAD sees a clean end even when HA cuts immediately after speech.
const silence = Buffer.alloc(24000 * 2 * 0.45);
await client.request("talk.session.appendAudio", {
  sessionId,
  audioBase64: silence.toString("base64"),
  timestamp,
});

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
if (!userFinal) throw new Error("Realtime Talk returned no final user transcript");
