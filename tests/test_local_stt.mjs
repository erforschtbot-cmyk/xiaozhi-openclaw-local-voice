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

// Pegel wie am echten Geraet beobachtet: Sprache liegt bei RMS 40-110,
// Stille bei RMS 14-20. Ein Test mit hohen Werten (z. B. 4000) wuerde eine
// viel zu hohe Schwelle nicht auffallen lassen.
const SPEECH_AMPLITUDE = 80; // RMS ~80
const QUIET_AMPLITUDE = 16;  // RMS ~16
const NOISE_AMPLITUDE = 30;  // RMS ~30, unter der Schwelle 35

// Stille -> Sprache -> Stille. Genau eine Aeusserung muss herauskommen.
const detector = new UtteranceDetector({ rate: RATE, silenceMs: 700, minSpeechMs: 200 });
let utterances = 0;
for (let index = 0; index < 20; index += 1) {          // 1,2 s Stille
  if (detector.push(frame(QUIET_AMPLITUDE))) utterances += 1;
}
for (let index = 0; index < 25; index += 1) {          // 1,5 s Sprache
  if (detector.push(frame(SPEECH_AMPLITUDE))) utterances += 1;
}
for (let index = 0; index < 30; index += 1) {          // 1,8 s Stille
  const result = detector.push(frame(QUIET_AMPLITUDE));
  if (result) utterances += 1;
}
check("Genau eine Aeusserung erkannt", utterances === 1, `gezaehlt: ${utterances}`);

// Reine Stille darf keine Aeusserung erzeugen.
const quietDetector = new UtteranceDetector({ rate: RATE });
let quietUtterances = 0;
for (let index = 0; index < 100; index += 1) {
  if (quietDetector.push(frame(QUIET_AMPLITUDE))) quietUtterances += 1;
}
check("Reine Stille ergibt keine Aeusserung", quietUtterances === 0);

// Zimmerrauschen darf nicht als Sprache gelten.
const noisyDetector = new UtteranceDetector({ rate: RATE });
let noisyUtterances = 0;
for (let index = 0; index < 100; index += 1) {
  if (noisyDetector.push(frame(NOISE_AMPLITUDE))) noisyUtterances += 1;
}
check("Zimmerrauschen ergibt keine Aeusserung", noisyUtterances === 0);

// Sehr leise Sprache muss trotzdem erkannt werden (Grenzfall).
const faintDetector = new UtteranceDetector({ rate: RATE });
let faintUtterances = 0;
for (let index = 0; index < 10; index += 1) faintDetector.push(frame(QUIET_AMPLITUDE));
for (let index = 0; index < 25; index += 1) {
  if (faintDetector.push(frame(45))) faintUtterances += 1;
}
for (let index = 0; index < 30; index += 1) {
  if (faintDetector.push(frame(QUIET_AMPLITUDE))) faintUtterances += 1;
}
check("Leise Sprache (RMS ~45) wird erkannt", faintUtterances === 1,
      `gezaehlt: ${faintUtterances}`);

// --------------------------------------------------------------------------
// Regression: kurzer lauter Transient darf den Detektor NICHT 20 s blockieren.
//
// Fehlerbild vor dem Fix: ein einzelner lauter Rahmen setzte speaking=true,
// sammelte aber nur speechMs < minSpeechMs. Weil die Endebedingung zusaetzlich
// speechMs >= minSpeechMs verlangte, griff sie nie; der Detektor sammelte bis
// maxUtteranceMs (20 s) und verwarf dann STILL. In diesen 20 s war die
// Erkennung blind ("Geraet hoert zu, es kommt nichts").
const discardLog = [];
const stuckDetector = new UtteranceDetector({
  rate: RATE,
  onDiscard: (info) => discardLog.push(info),
});
for (let index = 0; index < 10; index += 1) stuckDetector.push(frame(QUIET_AMPLITUDE));

// 2 laute Rahmen = 120 ms Sprache, danach dauerhafte Stille.
stuckDetector.push(frame(SPEECH_AMPLITUDE));
stuckDetector.push(frame(SPEECH_AMPLITUDE));
let stuckUtterances = 0;
let framesUntilReset = 0;
for (let index = 0; index < 40; index += 1) {          // 2,4 s Stille
  framesUntilReset += 1;
  if (stuckDetector.push(frame(QUIET_AMPLITUDE))) stuckUtterances += 1;
  if (!stuckDetector.speaking) break;
}
check("Kurzer Transient blockiert den Detektor nicht",
      framesUntilReset <= 20, `erst nach ${framesUntilReset} Rahmen frei`);
check("Kurzer Transient erzeugt keine Aeusserung", stuckUtterances === 0,
      `gezaehlt: ${stuckUtterances}`);
check("Verworfene Aeusserung wird gemeldet (onDiscard)", discardLog.length === 1,
      `Meldungen: ${discardLog.length}`);

// Nach dem Verwerfen muss eine echte Aeusserung wieder sauber erkannt werden.
let afterUtterances = 0;
for (let index = 0; index < 25; index += 1) {
  if (stuckDetector.push(frame(SPEECH_AMPLITUDE))) afterUtterances += 1;
}
for (let index = 0; index < 30; index += 1) {
  if (stuckDetector.push(frame(QUIET_AMPLITUDE))) afterUtterances += 1;
}
check("Nach dem Verwerfen wird die naechste Aeusserung erkannt",
      afterUtterances === 1, `gezaehlt: ${afterUtterances}`);

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
