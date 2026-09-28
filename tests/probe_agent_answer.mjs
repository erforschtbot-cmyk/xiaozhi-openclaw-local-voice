#!/usr/bin/env node
// Diagnose: zeigt die vollstaendige Struktur der chat-Ereignisse des Agenten.
//
// Klaert zwei Fragen:
//   1. In welcher Form kommt der Antworttext (delta? final? welches Feld?)
//   2. Warum war die Antwort im Betrieb leer ("Erledigt." ist der Rueckfall).
//
// Aufruf:  node tests/probe_agent_answer.mjs [session-key] [frage]

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

const SESSION_KEY = process.argv[2] || "agent:voice:xiaozhi-realtime-v5";
const QUESTION = process.argv[3] || "Wie spaet ist es?";

function gatewayToken() {
  const secretsFile = path.join(os.homedir(), ".openclaw", "secrets.json");
  const secrets = JSON.parse(fs.readFileSync(secretsFile, "utf8"));
  return secrets.gateway?.authToken;
}

let ready;
const readyPromise = new Promise((resolve) => {
  ready = resolve;
});

const client = new GatewayClient({
  url: "wss://127.0.0.1:18789",
  token: gatewayToken(),
  clientName: "cli",
  clientDisplayName: "Agent answer probe",
  clientVersion: "1.0.0",
  platform: "linux",
  mode: "cli",
  role: "operator",
  scopes: ["operator.read", "operator.write"],
  onHelloOk: ready,
  onConnectError: (error) => {
    console.error("connect error:", error);
    process.exit(1);
  },
  onEvent: (frame) => {
    if (frame.event !== "chat") return;
    const p = frame.payload;
    console.log(`--- chat event ---`);
    console.log(`  runId:   ${p?.runId}`);
    console.log(`  state:   ${p?.state}`);
    console.log(`  seq:     ${p?.seq}`);
    if (p?.text !== undefined) {
      console.log(`  text:    ${JSON.stringify(p.text).slice(0, 200)}`);
    }
    if (p?.message !== undefined) {
      console.log(`  message.type:  ${typeof p.message}`);
      if (typeof p.message === "string") {
        console.log(`  message:       ${JSON.stringify(p.message).slice(0, 200)}`);
      } else {
        console.log(`  message.role:  ${p.message?.role}`);
        console.log(`  message.content: ${JSON.stringify(p.message?.content).slice(0, 400)}`);
        console.log(`  message keys:  ${Object.keys(p.message ?? {}).join(", ")}`);
      }
    }
    if (p?.errorMessage) console.log(`  error:   ${p.errorMessage}`);
  },
});

client.start();
await readyPromise;
console.log(`Verbunden. Session: ${SESSION_KEY}`);
console.log(`Frage: ${QUESTION}`);
console.log();

const started = await client.request("chat.send", {
  sessionKey: SESSION_KEY,
  message: QUESTION,
  idempotencyKey: `probe-${Date.now()}`,
});
console.log(`chat.send -> ${JSON.stringify(started)}`);
console.log();

// Auf den Abschluss warten.
await new Promise((resolve) => setTimeout(resolve, 25000));
await client.stopAndWait({ timeoutMs: 3000 }).catch(() => {});
console.log();
console.log("fertig");
