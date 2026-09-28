#!/usr/bin/env node
// Lokaler Sprachhelfer: ersetzt den OpenAI-Realtime-Helfer vollstaendig.
//
// Aufgabe
// -------
// Er nimmt Mikrofon-Audio (PCM s16le, mono, 16 kHz) von stdin, erkennt
// Aeusserungsgrenzen, transkribiert lokal ueber den Wyoming-Whisper-Dienst
// und schickt den Text an den OpenClaw-Agenten. Dessen Antworttext meldet er
// nach stderr — dieselben Ereignisse, die der bisherige Helfer lieferte.
//
// Warum das den Provider ersetzt
// ------------------------------
// Bisher machte OpenAI drei Dinge: Sprache erkennen, das Gespraech fuehren
// und den Agenten bei Werkzeugbedarf aufrufen. Hier uebernimmt:
//   - Erkennung   -> lokaler Whisper-Dienst (Wyoming)
//   - Gespraech   -> der OpenClaw-Agent direkt (chat.send), inkl. Werkzeugen
// Es bleibt kein Schritt bei einem Cloud-Sprachdienst.
//
// Ereignisse auf stderr (identisch zum bisherigen Helfer):
//   {"type":"ready", ...}
//   {"type":"user_transcript","text":"..."}
//   {"type":"assistant_delta","text":"..."}
//   {"type":"assistant_done","text":"..."}
//
// Aufruf wie zuvor:  node local-voice.mjs 16000 2 1

import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { execFileSync } from "node:child_process";

import { transcribePcm, UtteranceDetector } from "./local_stt.mjs";

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
const WHISPER_URI = process.env.OPENCLAW_WHISPER_URI || "127.0.0.1:10300";
const LANGUAGE = process.env.OPENCLAW_WHISPER_LANGUAGE || "de";
const CONSULT_SESSION_KEY = process.env.OPENCLAW_TALK_CONSULT_SESSION_KEY || "";

function emit(type, data = {}) {
  process.stderr.write(`${JSON.stringify({ type, ...data })}\n`);
}

function log(message) {
  process.stderr.write(`[local-voice] ${message}\n`);
}

function gatewayToken() {
  const secretsFile =
    process.env.OPENCLAW_SECRETS_FILE ||
    path.join(os.homedir(), ".openclaw", "secrets.json");
  const secrets = JSON.parse(fs.readFileSync(secretsFile, "utf8"));
  const token = secrets.gateway?.authToken;
  if (!token) throw new Error("OpenClaw gateway auth token missing");
  return token;
}

function finalMessageText(message) {
  if (typeof message === "string") return message;
  const content = message?.content;
  if (!Array.isArray(content)) return "";
  return content
    .map((part) => (part?.type === "text" ? part.text ?? "" : ""))
    .join("");
}

/**
 * Zieht den Antworttext aus einem chat-Ereignis.
 *
 * Der Agent liefert den Text NICHT als `payload.text`, sondern als
 * `payload.message.content` (Liste von Teilen). Ein reiner `payload.text`-Zugriff
 * ergibt deshalb immer einen leeren Text — die Antwort verstummt und der
 * Rueckfall greift. Beide Formen werden hier abgedeckt.
 */
function extractText(payload) {
  if (!payload) return "";
  if (typeof payload.text === "string" && payload.text) return payload.text;
  return finalMessageText(payload.message);
}

const [, , rateText, widthText, channelsText] = process.argv;
const rate = Number(rateText);
const width = Number(widthText);
const channels = Number(channelsText);
if (!rate || width !== 2 || !channels) {
  throw new Error("Usage: local-voice.mjs RATE WIDTH CHANNELS");
}

// --------------------------------------------------------------------------
// Gateway-Anbindung
// --------------------------------------------------------------------------

let readyResolve;
let readyReject;
const ready = new Promise((resolve, reject) => {
  readyResolve = resolve;
  readyReject = reject;
});

const pendingRuns = new Map();
let activeRun = null;

const client = new GatewayClient({
  url: "wss://127.0.0.1:18789",
  token: gatewayToken(),
  clientName: "cli",
  clientDisplayName: "XiaoZhi Local Voice Helper",
  clientVersion: "1.0.0",
  platform: "linux",
  mode: "cli",
  role: "operator",
  scopes: ["operator.read", "operator.write"],
  onHelloOk: readyResolve,
  onConnectError: readyReject,
  onEvent: (frame) => {
    if (frame.event !== "chat") return;
    const payload = frame.payload;

    // Antworttext an die Bridge melden, damit sie lokal spricht.
    if (activeRun && payload?.runId === activeRun.runId) {
      if (payload.state === "delta") {
        const text = extractText(payload);
        const delta = takeDelta(text);
        if (delta) emit("assistant_delta", { text: delta });
        return;
      }
      if (payload.state === "final") {
        const full = extractText(payload);
        if (!full) {
          // Sichtbar machen, statt still zu verstummen.
          log(`final ohne Text; Rohdaten: ${JSON.stringify(payload).slice(0, 300)}`);
        }
        // Falls das Transkript nie als Delta kam, den Rest nachreichen.
        const tail = takeDelta(full);
        if (tail) emit("assistant_delta", { text: tail });
        const pending = pendingRuns.get(payload.runId);
        if (pending) {
          pendingRuns.delete(payload.runId);
          pending.resolve(full);
        }
        return;
      }
      if (payload.state === "error" || payload.state === "aborted") {
        const pending = pendingRuns.get(payload.runId);
        if (pending) {
          pendingRuns.delete(payload.runId);
          pending.reject(new Error(payload.errorMessage || "Agent run failed"));
        }
      }
    }
  },
});

// Der Agent liefert den Text je nach Modell kumulativ oder stueckweise.
let assistantText = "";
function takeDelta(text) {
  if (!text) return "";
  if (text.startsWith(assistantText)) {
    const delta = text.slice(assistantText.length);
    assistantText = text;
    return delta;
  }
  assistantText += text;
  return text;
}

client.start();
await Promise.race([
  ready,
  new Promise((_, reject) =>
    setTimeout(() => reject(new Error("Gateway connect timeout")), 10000),
  ),
]);

emit("ready", { sessionId: `local-voice`, consultSessionKey: null });

// --------------------------------------------------------------------------
// Aeusserungen erkennen, lokal transkribieren, an den Agenten geben
// --------------------------------------------------------------------------

const detector = new UtteranceDetector({
  rate,
  // Gemessene Werte am Gerät: Sprache RMS 40-110, Stille 14-20. Die
  // Schwelle muss deutlich darunter liegen, sonst wird Sprache verworfen.
  speechRms: Number(process.env.OPENCLAW_VAD_RMS || 35),
  silenceMs: Number(process.env.OPENCLAW_VAD_SILENCE_MS || 700),
  minSpeechMs: Number(process.env.OPENCLAW_VAD_MIN_SPEECH_MS || 200),
});

let busy = false;
let pendingUtterance = null;

// Wachwort allein darf keine Frage sein: Das Geraet schickt Mikrofon-Audio
// fortlaufend, deshalb wird auch das gesprochene "Jarvis" erkannt. Es ist
// kein Auftrag, sondern die Aktivierung.
const WAKE_ONLY = /^(jarvis|hey jarvis|ja vis|javis|harvis|charvis)$/;

function isWakeWordOnly(text) {
  const normalized = text
    .toLowerCase()
    .replace(/[^\p{L}\s]/gu, "")
    .replace(/\s+/g, " ")
    .trim();
  return normalized.length > 0 && WAKE_ONLY.test(normalized);
}

/**
 * Nimmt eine erkannte Aeusserung zur Verarbeitung an.
 *
 * Wichtig: `busy` wird SOFORT gesetzt, nicht erst nach der Transkription.
 * Sonst koennen zwei Aeusserungen gleichzeitig in die Verarbeitung laufen;
 * die zweite ueberschreibt dann die laufende Antwort und die erste geht
 * verloren. Ist bereits eine Aeusserung aktiv, wird die neue vorgemerkt
 * (die letzte gewinnt) und danach verarbeitet.
 */
function dispatchUtterance(frame) {
  if (busy) {
    pendingUtterance = frame;
    log(
      `Aeusserung vorgemerkt: ${(frame.length / 2 / rate).toFixed(2)} s ` +
      "(laufende Verarbeitung zuerst)",
    );
    return;
  }
  busy = true;
  void handleUtterance(frame);}

async function handleUtterance(pcm) {
  let text = "";
  try {
    text = await transcribePcm(pcm, {
      uri: WHISPER_URI,
      language: LANGUAGE,
      rate,
      width,
      channels,
    });
  } catch (error) {
    log(`Erkennung fehlgeschlagen: ${error.message}`);
    emit("error", { message: String(error.message || error) });
    return;
  }
  if (!text) {
    log("Leere Erkennung; keine Antwort angefordert");
    return;
  }
  if (isWakeWordOnly(text)) {
    log(`Wachwort erkannt, kein Auftrag: ${text}`);
    return;
  }

  emit("user_transcript", { text });
  log(`Erkannt: ${text}`);

  // Textzustand fuer die Delta-Berechnung zuruecksetzen.
  assistantText = "";
  try {
    const started = await client.request("chat.send", {
      sessionKey: SESSION_KEY,
      message: text,
      idempotencyKey: `xiaozhi-local-${Date.now()}`,
      // Kein `suppressCommandInterpretation`: dieses Feld verlangt
      // Admin-Rechte am Gateway, die der Token hier nicht hat. Fuer eine
      // gesprochene Aeusserung ist es nicht erforderlich.
    });
    const runId = started.runId ?? started.idempotencyKey;
    activeRun = { runId };
    const answer = await new Promise((resolve, reject) => {
      pendingRuns.set(runId, { resolve, reject });
    });
    if (!answer) {
      // Ohne Text nicht sprechen; die Bridge soll trotzdem sauber abschliessen.
      log("Leere Antwort; kein Sprechauftrag");
      emit("assistant_done", {
        text: "",
        audioFrameCount: 0,
        audioByteCount: 0,
        msSinceLastAudio: null,
      });
      return;
    }
    emit("assistant_done", {
      text: answer,
      audioFrameCount: 0,
      audioByteCount: 0,
      msSinceLastAudio: null,
    });
    log(`Antwort: ${answer.slice(0, 120)}`);
  } catch (error) {
    log(`Agent-Aufruf fehlgeschlagen: ${error.message}`);
    emit("error", { message: String(error.message || error) });
  } finally {
    activeRun = null;
    busy = false;
    // Eine waehrend der Verarbeitung erkannte Aeusserung jetzt nachholen.
    const next = pendingUtterance;
    pendingUtterance = null;
    if (next) dispatchUtterance(next);
  }
}

// Stdin als PCM-Strom lesen.
let pending = Buffer.alloc(0);
let framesSeen = 0;
const frameBytes = Math.floor((rate * 60) / 1000) * 2; // 60-ms-Rahmen

process.stdin.on("data", (chunk) => {
  pending = Buffer.concat([pending, chunk]);
  while (pending.length >= frameBytes) {
    const frame = pending.subarray(0, frameBytes);
    pending = pending.subarray(frameBytes);
    framesSeen += 1;
    if (framesSeen % 25 === 0) {
      // Diagnose: zeigt, ob ueberhaupt Audio ankommt und wie laut es ist.
      const recent = detector.rmsSamples;
      const avg = recent.length
        ? (recent.reduce((a, b) => a + b, 0) / recent.length).toFixed(0)
        : "-";
      log(
        `frames=${framesSeen} rms=${detector.lastRms.toFixed(0)} ` +
        `avg=${avg} threshold=${(detector.lastThreshold ?? 0).toFixed(0)} ` +
        `speaking=${detector.speaking} speechMs=${detector.speechMs.toFixed(0)} ` +
        `silence=${detector.silenceMsAccum.toFixed(0)} busy=${busy}`,
      );
    }
    const utterance = detector.push(frame);
    if (utterance) {
      log(`Aeusserung erkannt: ${(utterance.length / 2 / rate).toFixed(2)} s`);
      dispatchUtterance(utterance);
    }
  }
});

/**
 * Schliesst eine noch offene Aeusserung ab.
 *
 * Das Geraet beendet eine Aufnahme in der Regel mit einem `listen stop`,
 * ohne abschliessende Stille zu senden. Dann kann der Detektor kein Ende
 * erkennen. Hier wird deshalb synthetische Stille eingespeist, bis die
 * Aeusserung als beendet gilt.
 */
async function flushUtterance() {
  if (busy) return;
  const silence = Buffer.alloc(frameBytes);
  const maxFrames = Math.ceil(3000 / 60); // bis zu 3 s Stille
  for (let index = 0; index < maxFrames; index += 1) {
    const utterance = detector.push(silence);
    if (utterance) {
      await handleUtterance(utterance);
      return;
    }
    if (!detector.speaking) {
      // Es wurde nie Sprache erkannt; nichts abzuschliessen.
      log("Keine offene Aeusserung beim Schliessen");
      return;
    }
  }
  log("Aeusserung beim Schliessen nicht abgeschlossen");
}

process.stdin.on("end", () => {
  log("stdin geschlossen; schliesse letzte Aeusserung ab");
  void (async () => {
    await flushUtterance();
    while (busy) await new Promise((resolve) => setTimeout(resolve, 50));
    await client.stopAndWait({ timeoutMs: 3000 }).catch(() => {});
    process.exit(0);
  })();
});

// Solange leben, bis der Aufrufer beendet (wie der bisherige Helfer).
setInterval(() => {}, 1 << 30);
