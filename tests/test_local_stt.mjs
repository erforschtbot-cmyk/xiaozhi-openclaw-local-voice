#!/usr/bin/env node
// Test fuer das lokale Erkenner-Modul (gateway/local_stt.mjs).
//
// Zwei Ebenen:
//   1. UtteranceDetector: rein rechnerisch, ohne Dienst.
//   2. transcribePcm: gegen den laufenden Wyoming-Whisper-Dienst.
//
// Aufruf:  node tests/test_local_stt.mjs [wav-datei]

import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { transcribePcm, UtteranceDetector } from "../gateway/local_stt.mjs";

const HERE = path.dirname(fileURLToPath(import.meta.url));
const WAV = process.argv[2] || "/tmp/stt_probe.wav";

const failures = [];

function check(name, condition, detail = "") {
  console.log(`  [${condition ? "OK  " : "FEHL"}] ${name}${detail ? " — " + detail : ""}`);
  if (!condition) failures.push(name);
}

// --------------------------------------------------------------------------
console.log("=== 1. Aeusserungsgrenzen (UtteranceDetector) ===");

const RATE = 16000;
const frameMs = 60;
const frameBytes = Math.floor((RATE * frameMs) / 1000) * 2;

/** Erzeugt einen Rahmen mit gegebener Amplitude. */
function frame(amplitude) {
  const buf = Buffer.alloc(frameBytes);
  for (let offset = 0; offset < frameBytes; offset += 2) {
    buf.writeInt16LE(amplitude, offset);
  }
  return buf;
}

// Stille -> Sprache -> Stille. Genau eine Aeusserung muss herauskommen.
const detector = new UtteranceDetector({ rate: RATE, silenceMs: 700, minSpeechMs: 250 });
let utterances = 0;
let quiet = 0;
for (let index = 0; index < 20; index += 1) {          // 1,2 s Stille
  if (detector.push(frame(0))) utterances += 1;
}
for (let index = 0; index < 25; index += 1) {          // 1,5 s Sprache
  if (detector.push(frame(4000))) utterances += 1;
}
for (let index = 0; index < 30; index += 1) {          // 1,8 s Stille
  const result = detector.push(frame(0));
  if (result) utterances += 1;
}
check("Genau eine Aeusserung erkannt", utterances === 1, `gezaehlt: ${utterances}`);

// Reine Stille darf keine Aeusserung erzeugen.
const quietDetector = new UtteranceDetector({ rate: RATE });
let quietUtterances = 0;
for (let index = 0; index < 100; index += 1) {
  if (quietDetector.push(frame(0))) quietUtterances += 1;
}
check("Reine Stille ergibt keine Aeusserung", quietUtterances === 0);

// Grundrauschen darf nicht als Sprache gelten (leises Brummen).
const noisyDetector = new UtteranceDetector({ rate: RATE, speechRms: 420 });
let noisyUtterances = 0;
for (let index = 0; index < 100; index += 1) {
  if (noisyDetector.push(frame(80))) noisyUtterances += 1;
}
check("Leises Grundrauschen ergibt keine Aeusserung", noisyUtterances === 0);

// --------------------------------------------------------------------------
console.log();
console.log("=== 2. Erkennung ueber den lokalen Dienst ===");

if (!fs.existsSync(WAV)) {
  check("Testaudio vorhanden", false, `${WAV} fehlt`);
} else {
  const wav = fs.readFileSync(WAV);
  // Kopf ueberspringen und reine PCM-Daten nehmen.
  let dataOffset = 44;
  const dataTag = wav.indexOf(Buffer.from("data"));
  if (dataTag !== -1) dataOffset = dataTag + 8;
  const pcm = wav.subarray(dataOffset);

  const started = Date.now();
  try {
    const text = await transcribePcm(pcm, { uri: "127.0.0.1:10300", language: "de" });
    const elapsed = (Date.now() - started) / 1000;
    const audioSeconds = pcm.length / 2 / RATE;
    console.log(`  Text: ${JSON.stringify(text)}`);
    console.log(`  ${audioSeconds.toFixed(2)} s Audio in ${elapsed.toFixed(2)} s ` +
                `(RTF ${(elapsed / audioSeconds).toFixed(2)})`);
    check("Text erkannt", text.length > 0);
    check("Schluesselwort 'Monitor' enthalten", /monitor/i.test(text));
    check("Schneller als Echtzeit (RTF < 1)", elapsed / audioSeconds < 1.0);
  } catch (error) {
    check("Erkennung ohne Fehler", false, String(error.message || error));
  }
}

console.log();
if (failures.length) {
  console.log(`FEHLGESCHLAGEN: ${failures.join(", ")}`);
  process.exit(1);
}
console.log("Alle Tests bestanden.");
