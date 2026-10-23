#!/usr/bin/env node
// Schlanker Realtime-Helfer fuer das XiaoZhi-Geraet.
//
// Warum es diese Datei gibt
// -------------------------
// Der bisherige Helfer nutzte `talk.session.create` mit `gateway-relay`. Dabei
// gehoert die Medienverbindung dem Gateway, das sie intern an OpenAI
// weiterreicht. In dieser Zwischenschicht sassen alle belegten Ausfaelle:
// `Unknown Talk session`, "Realtime provider output has no live response
// owner", verlorene Sitzungen nach langen Pausen.
//
// OpenClaw dokumentiert fuer genau diesen Fall einen anderen Weg
// (docs/nodes/talk/realtime-sessions.md, "Thin audio clients"):
//
//   capabilities: ["gateway-control-v1"]
//   -> clientControl: { owner: "gateway" }
//   -> clientSecret + offerUrl "/plugins/openai/realtime/calls"
//
// Der Client schickt nur ein Audio-SDP-Angebot. **Die Medien laufen direkt
// zwischen diesem Prozess und OpenAI.** Der Gateway besitzt weiterhin
// Werkzeuge, Transkripte, Steuerung und Aufraeumen. Damit faellt die
// Zwischenschicht weg, die die Fehler erzeugt hat.
//
// Audioformat
// -----------
//   Geraet -> hier : PCM s16le, 16 kHz, mono   (aus Opus dekodiert)
//   hier -> OpenAI : PCM s16le, 24 kHz, mono   (Peer resampelt auf 48 kHz)
//   OpenAI -> hier : PCM s16le, 24 kHz, mono   (Peer resampelt von 48 kHz)
//   hier -> Geraet : PCM s16le, 24 kHz, mono   (wird wieder zu Opus)
//
// Aufruf wie bisher: node openclaw-thin-client.mjs 16000 2 1

import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { execFileSync } from "node:child_process";

const OPENCLAW_NPM_ROOT =
  process.env.OPENCLAW_NPM_ROOT ||
  execFileSync("npm", ["root", "-g"], { encoding: "utf8" }).trim();
const DIST = path.join(OPENCLAW_NPM_ROOT, "openclaw", "dist");

const { GatewayClient } = await import(
  pathToFileURL(path.join(DIST, "plugin-sdk/gateway-runtime.js")).href
);
const { t: OpenAIQuicksilverAudioPeer } = await import(
  pathToFileURL(path.join(DIST, "realtime-quicksilver-media.runtime-ByhwKKTt.mjs")).href
);

const GATEWAY_URL = process.env.OPENCLAW_GATEWAY_URL || "wss://127.0.0.1:18789";
const GATEWAY_HTTP = process.env.OPENCLAW_GATEWAY_HTTP || "https://127.0.0.1:18789";
const SESSION_KEY =
  process.env.OPENCLAW_TALK_SESSION_KEY || "agent:voice:voice-assistant";
const MODEL = process.env.OPENCLAW_REALTIME_MODEL || "gpt-live-1-codex";
const VOICE = process.env.OPENCLAW_REALTIME_VOICE || "cove";
const CA = process.env.NODE_EXTRA_CA_CERTS || "";

function emit(type, data = {}) {
  process.stderr.write(`${JSON.stringify({ type, ...data })}\n`);
}

// Diagnose sammelt mit, blockiert aber nie. Ohne sie ist ein Ausfall nur an
// FEHLENDEN Zeilen erkennbar — genau das hat die Suche tagelang erschwert.
const LOG_DIR =
  process.env.OPENCLAW_FLIGHT_DIR ||
  "/home/openclaw/.local/share/jarvis-realtime-bridge/logs";
const START = Date.now();
let logPath = null;
try {
  fs.mkdirSync(LOG_DIR, { recursive: true, mode: 0o700 });
  const stamp = new Date(START).toISOString().replace(/[:.]/g, "-");
  logPath = path.join(LOG_DIR, `thin-${stamp}-${process.pid}.jsonl`);
} catch {
  logPath = null;
}
let logged = 0;
function record(stage, data = {}) {
  if (!logPath || logged >= 600) return;
  logged += 1;
  try {
    const extra = {};
    for (const [k, v] of Object.entries(data)) extra[`d${k}`] = v;
    fs.appendFileSync(
      logPath,
      JSON.stringify({
        at: new Date().toISOString(),
        sinceStartMs: Date.now() - START,
        stage,
        ...extra,
      }) + "\n",
    );
  } catch {
    /* Diagnose darf den Sprachweg nie stören */
  }
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

// --- Audio: 16 kHz mono -> 24 kHz mono (linear) ---------------------------
function resample16to24(input) {
  const frames = Math.floor(input.length / 2);
  const outFrames = Math.floor((frames * 3) / 2);
  const out = Buffer.alloc(outFrames * 2);
  for (let i = 0; i < outFrames; i += 1) {
    const pos = (i * 2) / 3;
    const lo = Math.floor(pos);
    const hi = Math.min(lo + 1, frames - 1);
    const frac = pos - lo;
    const a = input.readInt16LE(lo * 2);
    const b = input.readInt16LE(hi * 2);
    out.writeInt16LE(Math.max(-32768, Math.min(32767, Math.round(a * (1 - frac) + b * frac))), i * 2);
  }
  return out;
}

const rate = Number(process.argv[2] || 16000);
const channels = Number(process.argv[4] || 1);
if (!rate || channels !== 1) {
  throw new Error("Usage: openclaw-thin-client.mjs RATE 2 CHANNELS(1)");
}

// --- Gateway-Verbindung --------------------------------------------------
let helloResolve;
let helloReject;
const hello = new Promise((resolve, reject) => {
  helloResolve = resolve;
  helloReject = reject;
});

let voiceSessionId = null;
let peer = null;
let closed = false;
let userText = "";
let lastUserPartial = "";
let assistantText = "";
// Fertige Antwort (output.text.done). Vorher ist der Text nur ein Zwischenstand
// und darf den Turn NICHT beenden: belegt am 22.10.2026, 20:54 - der Helfer
// brach 11 ms nach dem Transkript ab, weil schon ein Textfragment vorlag, und
// die Antwort war nie zu hoeren.
let assistantFinalDone = false;
let sentBytes = 0;
let receivedBytes = 0;
let pendingFinal = null;
// Diagnose: Kam je Provider-Audio an, und wann war der Peer bereit?
let providerAudioSeen = false;
let peerReadyAt = 0;

const client = new GatewayClient({
  url: GATEWAY_URL,
  token: gatewayToken(),
  clientName: "cli",
  clientDisplayName: "Jarvis XiaoZhi thin voice client",
  clientVersion: "1.0.0",
  platform: "linux",
  mode: "cli",
  role: "operator",
  scopes: ["operator.read", "operator.write"],
  onHelloOk: helloResolve,
  onConnectError: helloReject,
  onEvent: (frame) => {
    if (frame.event !== "talk.event") return;
    const payload = frame.payload;
    if (!payload || payload.voiceSessionId !== voiceSessionId) return;
    const ev = payload.talkEvent;
    if (!ev) return;
    // Fehler- und Close-Ereignisse vollstaendig mitschreiben.
    //
    // Vorher stand hier nur type: "session.error" — der Grund wurde
    // weggeworfen, und ein Ausfall war danach nicht mehr aufklaerbar
    // (belegt 23.10.2026, 14:14:33: "transport failed", receivedBytes=0,
    // Ursache unbekannt, weil nicht gespeichert).
    if (ev.type === "session.error" || ev.type === "error" || ev.type === "session.closed") {
      let raw = "";
      try { raw = JSON.stringify(ev).slice(0, 1500); } catch { raw = String(ev); }
      record("talk_error_detail", {
        type: ev.type,
        raw,
        receivedBytes,
        sentBytes,
        providerAudioSeen,
      });
    } else {
      record("talk_event", { type: ev.type, role: ev.payload?.role ?? null });
    }

    if (ev.type === "session.closed") {
      // Der Gateway hat die Sitzung beendet. Lokale Medien schliessen, sonst
      // laeuft der Peer weiter und haelt Ressourcen.
      void shutdown("session_closed");
      return;
    }
    if (ev.type === "transcript.done" && ev.payload?.role === "user") {
      userText = String(ev.payload.text ?? "").trim();
      if (userText) emit("user_transcript", { text: userText });
      return;
    }
    // Live-Ticker: der Provider liefert den bisher gesprochenen Text
    // fortlaufend. Ohne diese Weitergabe erscheint der Satz erst am Ende.
    if (ev.type === "transcript.delta" && ev.payload?.role === "user") {
      const partial = String(ev.payload.text ?? "").trim();
      if (partial && partial !== lastUserPartial) {
        lastUserPartial = partial;
        emit("user_partial", { text: partial });
      }
      return;
    }
    if (ev.type === "output.text.delta") {
      const full = String(ev.payload?.text ?? "");
      const delta = full.startsWith(assistantText) ? full.slice(assistantText.length) : full;
      assistantText = full;
      if (delta.trim()) emit("assistant_delta", { text: delta });
      return;
    }
    if (ev.type === "output.text.done") {
      assistantText = String(ev.payload?.text ?? "").trim();
      assistantFinalDone = true;
      if (assistantText) emit("assistant_done", { text: assistantText });
      if (pendingFinal) {
        const done = pendingFinal;
        pendingFinal = null;
        done();
      }
      return;
    }
  },
});

async function createCall() {
  const ticket = await client.request(
    "talk.client.create",
    {
      sessionKey: SESSION_KEY,
      provider: "openai",
      model: MODEL,
      voice: VOICE,
      mode: "realtime",
      transport: "webrtc",
      brain: "agent-consult",
      // KEIN language-Feld: talk.client.create lehnt es ab (closedObject,
      // belegt 22.10.2026: "unexpected property 'language'"). Der Sprachhinweis
      // fuer die Realtime-Transkription fehlt in OpenClaw selbst (Issues
      // #175951/#176044); talk.speechLocale gilt nur fuer Geraete-Nodes und
      // die iOS-Systemstimme, nicht fuer diesen Pfad.
      // Genau dieser Zusatz macht aus dem Browser-Weg den schlanken
      // Audio-Client: keine Datenverbindung, Medien direkt zu OpenAI.
      capabilities: ["gateway-control-v1"],
    },
    { timeoutMs: 20000 },
  );
  record("call_created", {
    voiceSessionId: ticket.voiceSessionId,
    transport: ticket.transport,
    owner: ticket.clientControl?.owner ?? null,
    model: ticket.model ?? null,
    voice: ticket.voice ?? null,
  });
  return ticket;
}

async function postOffer(ticket, sdp) {
  const url = new URL(ticket.offerUrl, GATEWAY_HTTP).toString();
  const res = await fetch(url, {
    method: "POST",
    headers: {
      authorization: `Bearer ${ticket.clientSecret}`,
      "content-type": "application/sdp",
    },
    body: sdp,
  });
  const text = await res.text();
  record("offer_posted", { status: res.status, bytes: text.length });
  if (!res.ok) {
    throw new Error(`offer rejected (${res.status}): ${text.slice(0, 200)}`);
  }
  return text;
}

async function startPeer(ticket) {
  const created = await OpenAIQuicksilverAudioPeer.create({
    callbacks: {
      onAudio: (pcm) => {
        if (closed || !pcm?.length) return;
        if (!providerAudioSeen) {
          providerAudioSeen = true;
          record("first_peer_audio", {
            afterPeerMs: Date.now() - peerReadyAt,
            bytes: pcm.length,
          });
        }
        receivedBytes += pcm.length;
        process.stdout.write(pcm);
      },
      onError: (error) => {
        record("peer_error", {
          message: String(error?.message || error).slice(0, 400),
          name: String(error?.name || ""),
          stack: String(error?.stack || "").slice(0, 1200),
          receivedBytes,
          providerAudioSeen,
        });
        emit("error", { message: String(error?.message || error) });
        void shutdown("peer_error");
      },
      onMediaError: (error) => {
        record("media_error", {
          message: String(error?.message || error).slice(0, 400),
          receivedBytes,
        });
      },
    },
  });
  const offer = await created.createOffer();
  const answer = await postOffer(ticket, offer);
  await created.applyAnswer(answer);
  peerReadyAt = Date.now();
  record("peer_ready", {});
  return created;
}

async function shutdown(reason) {
  if (closed) return;
  closed = true;
  record("shutdown", { reason, sentBytes, receivedBytes, providerAudioSeen });
  try {
    peer?.close();
  } catch {
    /* ignore */
  }
  try {
    if (voiceSessionId) {
      await client.request(
        "talk.client.close",
        { sessionKey: SESSION_KEY, voiceSessionId },
        { timeoutMs: 4000 },
      );
      record("call_closed", {});
    }
  } catch (error) {
    record("call_close_failed", { message: String(error?.message || error).slice(0, 160) });
  }
  await client.stopAndWait({ timeoutMs: 2000 }).catch(() => {});
  if (logPath) process.stderr.write(`[thin] ${logPath}\n`);
  process.exit(0);
}

process.once("SIGTERM", () => void shutdown("sigterm"));
process.once("SIGINT", () => void shutdown("sigint"));

// --- Start ---------------------------------------------------------------
client.start();
await Promise.race([
  hello,
  new Promise((_, reject) =>
    setTimeout(() => reject(new Error("Gateway connect timeout")), 10000),
  ),
]);
record("gateway_ready", {});

const ticket = await createCall();
voiceSessionId = ticket.voiceSessionId;
peer = await startPeer(ticket);
emit("ready", { sessionId: voiceSessionId, mode: "thin-webrtc" });

// --- Mikrofon -> OpenAI --------------------------------------------------
let carry = Buffer.alloc(0);
for await (const chunk of process.stdin) {
  if (closed) break;
  const part = Buffer.from(chunk);
  const pcm = rate === 24000 ? part : resample16to24(part);
  if (pcm.length < 2) continue;
  sentBytes += pcm.length;
  peer.sendAudio(pcm);
  carry = carry.subarray(0);
}

// Ende der Aufnahme: kurze Stille, damit die Erkennung sicher abschliesst.
if (!closed) {
  peer.sendAudio(Buffer.alloc(24000 * 2 * 0.4));
  record("input_closed", { sentBytes });
  // Auf die FERTIGE Antwort warten (output.text.done), nicht auf das erste
  // Textfragment. Zusaetzlich auf das Ende des Audioausgangs, damit die
  // Wiedergabe nicht mitten im Satz abbricht.
  const deadline = Date.now() + 45000;
  while (!closed && !assistantFinalDone && Date.now() < deadline) {
    await new Promise((r) => setTimeout(r, 50));
  }
  // Kurz nachlaufen lassen, damit das letzte Audio den Peer verlaesst.
  if (assistantFinalDone) {
    await new Promise((r) => setTimeout(r, 1200));
  }
  record("wait_finished", {
    assistantFinalDone,
    receivedBytes,
    waitedMs: Date.now() - START,
  });
  await shutdown(assistantFinalDone ? "answered" : "no_answer");
}
