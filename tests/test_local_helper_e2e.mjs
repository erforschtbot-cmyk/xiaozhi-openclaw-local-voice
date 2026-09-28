#!/usr/bin/env node
// Ende-zu-Ende-Test des lokalen Sprachhelfers (gateway/local-voice.mjs).
//
// Er speist echtes Sprachaudio in den Helfer ein und prueft, dass die Kette
// ohne Cloud-Sprachdienst funktioniert:
//
//   Audio -> Erkennung (lokaler Whisper) -> Agent -> Antworttext
//
// Der Agent selbst laeuft ueber das Gateway (wie bisher). Verlangt wird nur,
// dass Erkennung und Antworttext auf dem lokalen Weg entstehen.
//
// Aufruf:  node tests/test_local_helper_e2e.mjs [wav-datei]

import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO = path.resolve(HERE, "..");
const HELPER = path.join(REPO, "gateway", "local-voice.mjs");
const WAV = process.argv[2] || "/tmp/stt_probe.wav";

const failures = [];
const events = [];

function check(name, condition, detail = "") {
  console.log(`  [${condition ? "OK  " : "FEHL"}] ${name}${detail ? " — " + detail : ""}`);
  if (!condition) failures.push(name);
}

if (!fs.existsSync(WAV)) {
  console.error(`Testaudio fehlt: ${WAV}`);
  process.exit(1);
}

const wav = fs.readFileSync(WAV);
const dataTag = wav.indexOf(Buffer.from("data"));
const pcm = dataTag !== -1 ? wav.subarray(dataTag + 8) : wav.subarray(44);
console.log(`Audio: ${(pcm.length / 2 / 16000).toFixed(2)} s`);

// Der Gateway laeuft mit selbstsigniertem Zertifikat; im Betrieb reicht
// server.py der Helferin die CA ueber NODE_EXTRA_CA_CERTS durch. Der Test
// muss dasselbe tun, sonst bricht der TLS-Handschlag ab.
const gatewayCa =
  process.env.OPENCLAW_GATEWAY_CA ||
  path.join(os.homedir(), ".openclaw", "ssl", "gateway.crt");
if (!fs.existsSync(gatewayCa)) {
  console.error(`Gateway-CA fehlt: ${gatewayCa}`);
  process.exit(1);
}

const child = spawn("node", [HELPER, "16000", "2", "1"], {
  env: {
    ...process.env,
    // Eigene Test-Session, damit der Produktivpfad unberuehrt bleibt.
    OPENCLAW_TALK_SESSION_KEY: "agent:voice:lokal-test",
    OPENCLAW_TALK_CONSULT_SESSION_KEY: "",
    OPENCLAW_WHISPER_URI: "127.0.0.1:10300",
    NODE_EXTRA_CA_CERTS: gatewayCa,
  },
  stdio: ["pipe", "pipe", "pipe"],
});

let stderrBuffer = "";
child.stderr.on("data", (chunk) => {
  stderrBuffer += chunk.toString("utf8");
  for (;;) {
    const newline = stderrBuffer.indexOf("\n");
    if (newline === -1) break;
    const line = stderrBuffer.slice(0, newline).trim();
    stderrBuffer = stderrBuffer.slice(newline + 1);
    if (!line) continue;
    if (line.startsWith("{")) {
      try {
        events.push(JSON.parse(line));
      } catch {
        console.log(`  [log] ${line}`);
      }
    } else {
      console.log(`  [log] ${line}`);
    }
  }
});

const started = Date.now();

// Audio einspeisen, danach Stille, damit die Aeusserung sicher endet.
child.stdin.write(pcm);
child.stdin.write(Buffer.alloc(16000 * 2 * 2)); // 2 s Stille

const deadline = Date.now() + 90000;
let done = false;
while (Date.now() < deadline && !done) {
  await new Promise((resolve) => setTimeout(resolve, 250));
  done = events.some((event) => event.type === "assistant_done");
}
const elapsed = (Date.now() - started) / 1000;

child.kill("SIGTERM");
await new Promise((resolve) => {
  child.once("exit", resolve);
  setTimeout(resolve, 3000);
});

console.log();
console.log(`=== Ereignisse (${elapsed.toFixed(1)} s) ===`);
const ready = events.find((event) => event.type === "ready");
const user = events.find((event) => event.type === "user_transcript");
const deltas = events.filter((event) => event.type === "assistant_delta");
const final = events.find((event) => event.type === "assistant_done");
const errors = events.filter((event) => event.type === "error");

for (const event of events) {
  console.log(`  ${event.type}: ${JSON.stringify(event.text ?? "").slice(0, 140)}`);
}

console.log();
check("Helfer meldet ready", Boolean(ready));
check("Sprache wurde lokal erkannt", Boolean(user && user.text.length > 0),
      user ? JSON.stringify(user.text) : "kein Transkript");
check("Erkannter Text enthaelt 'Monitor'", Boolean(user && /monitor/i.test(user.text)));
check("Antworttext kam stueckweise oder ganz", deltas.length > 0 || Boolean(final));
check("Antwort ist abgeschlossen", Boolean(final && final.text.length > 0),
      final ? JSON.stringify(final.text.slice(0, 80)) : "keine Antwort");
check("Kein Fehlerereignis", errors.length === 0,
      errors.map((e) => e.message).join("; ") || "");

console.log();
if (failures.length) {
  console.log(`FEHLGESCHLAGEN: ${failures.join(", ")}`);
  process.exit(1);
}
console.log("Ende-zu-Ende-Test des lokalen Helfers bestanden.");
