#!/usr/bin/env node
// Test fuer die Wachwort-Bereinigung in gateway/local-voice.mjs.
//
// Die Faelle stammen ausnahmslos aus echten Transkripten des Geraets
// (Bridge-Log vom 2026-09-28), nicht ausgedacht. Sie zeigen, wie
// unterschiedlich Whisper das gesprochene "Jarvis" schreibt.

import { cleanTranscript } from "../gateway/local_stt.mjs";

const failures = [];

function check(text, expected) {
  const got = cleanTranscript(text);
  const ok = got === expected;
  console.log(
    `  [${ok ? "OK  " : "FEHL"}] ${JSON.stringify(text).padEnd(42)} -> ` +
      `${JSON.stringify(got)}${ok ? "" : ` (erwartet ${JSON.stringify(expected)})`}`,
  );
  if (!ok) failures.push(text);
}

console.log("=== Reine Wachwort-Reste: muessen verschwinden ===");
check("Jarvis.", "");
check("Jarvis", "");
check("Hey Jarvis", "");
check("Job es.", "");
check("Ja, das...", "");
check("Ja, das", "");
check("Jarvis!", "");
check("ja, bis", "");

console.log();
console.log("=== Wachwort + Auftrag: Wachwort weg, Auftrag bleibt ===");
check("Jarvis, wie spät ist es?", "wie spät ist es?");
check("Jarvis, erzähle einen Witz.", "erzähle einen Witz.");
check("Hey Jarvis, schalte den Monitor an.", "schalte den Monitor an.");
check("Jarvis schalte den Monitor aus", "schalte den Monitor aus");
check("Jarwis, wie ist das Wetter?", "wie ist das Wetter?");

console.log();
console.log("=== Echte Auftraege: duerfen NICHT angetastet werden ===");
check("Wie spät ist das?", "Wie spät ist das?");
check("Erzähle einen Witz.", "Erzähle einen Witz.");
check("Schalte den Monitor an.", "Schalte den Monitor an.");
check("Ja, das ist gut.", "Ja, das ist gut.");
check("Wie ist das Wetter morgen?", "Wie ist das Wetter morgen?");

console.log();
if (failures.length) {
  console.log(`FEHLGESCHLAGEN: ${failures.length} Fall/Faelle`);
  process.exit(1);
}
console.log("Alle Faelle bestanden.");
