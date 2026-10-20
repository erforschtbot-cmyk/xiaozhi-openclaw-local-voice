#!/usr/bin/env node
// Einmaliger Foto-Consult: schickt ein Bild plus Frage an den OpenClaw-Agenten
// und gibt dessen Antworttext auf stdout aus.
//
// Aufruf:  node photo-consult.mjs <sessionKey> <imagePath> <mimeType> <frage...>
// Ausgabe: reiner Antworttext (eine Zeile), Fehler auf stderr + Exit 1.
//
// Warum ein eigener Prozess: Die Bridge spricht ueber den Realtime-Helfer mit
// dem Gateway. Fuer ein Bild braucht sie einen chat.send mit attachments —
// das bietet nur die Gateway-Client-API, nicht der Realtime-Pfad.

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

function gatewayToken() {
  const secretsFile =
    process.env.OPENCLAW_SECRETS_FILE ||
    path.join(os.homedir(), ".openclaw", "secrets.json");
  const token = JSON.parse(fs.readFileSync(secretsFile, "utf8")).gateway?.authToken;
  if (!token) throw new Error("OpenClaw gateway auth token missing");
  return token;
}

function finalText(message) {
  if (typeof message === "string") return message;
  const content = message?.content;
  if (!Array.isArray(content)) return "";
  return content
    .map((part) => (part?.type === "text" ? part.text ?? "" : ""))
    .join("");
}

const [, , sessionKey, imagePath, mimeType, ...questionParts] = process.argv;
if (!sessionKey || !imagePath || !mimeType) {
  console.error("Usage: photo-consult.mjs <sessionKey> <imagePath> <mime> <frage>");
  process.exit(2);
}
const question = questionParts.join(" ").trim();
const image = fs.readFileSync(imagePath);

let settled = false;
const done = new Promise((resolve, reject) => {
  const client = new GatewayClient({
    url: "wss://127.0.0.1:18789",
    token: gatewayToken(),
    clientName: "cli",
    clientDisplayName: "Jarvis photo consult",
    clientVersion: "1.0.0",
    platform: "linux",
    mode: "cli",
    role: "operator",
    scopes: ["operator.read", "operator.write"],
    onHelloOk: async () => {
      try {
        const started = await client.request("chat.send", {
          sessionKey,
          message: [
            "Der Nutzer hat gerade ein Foto machen lassen. Beantworte seine Frage dazu auf Deutsch, kurz und direkt.",
            "Beschreibe nur, was auf dem Bild tatsaechlich zu erkennen ist. Rate nicht.",
            `Frage: ${question || "Was ist auf dem Bild zu sehen?"}`,
          ].join("\n"),
          idempotencyKey: `jarvis-photo-${Date.now()}`,
          // Kein suppressCommandInterpretation: das Feld verlangt Admin-Rechte
          // ("system provenance fields require admin scope") und wird fuer
          // eine Bildfrage nicht gebraucht.
          attachments: [
            {
              type: "image",
              mimeType,
              fileName: "kamera.jpg",
              content: image.toString("base64"),
            },
          ],
        });
        pendingRunId = started.runId ?? started.idempotencyKey;
      } catch (error) {
        if (!settled) {
          settled = true;
          reject(error);
        }
      }
    },
    onConnectError: (error) => {
      if (!settled) {
        settled = true;
        reject(error);
      }
    },
    onEvent: (frame) => {
      if (frame.event !== "chat") return;
      const payload = frame.payload;
      if (!pendingRunId || payload?.runId !== pendingRunId) return;
      if (payload.state === "final") {
        if (settled) return;
        settled = true;
        resolve(finalText(payload.message) || "Erledigt.");
      } else if (payload.state === "error" || payload.state === "aborted") {
        if (settled) return;
        settled = true;
        reject(new Error(payload.errorMessage || "Agent run failed"));
      }
    },
  });
  client.start();
});

let pendingRunId = null;
const timeout = new Promise((_, reject) =>
  setTimeout(() => reject(new Error("photo consult timeout")), 90000),
);

try {
  const text = await Promise.race([done, timeout]);
  process.stdout.write(String(text).trim() + "\n");
  process.exit(0);
} catch (error) {
  console.error(String(error?.message ?? error));
  process.exit(1);
}
