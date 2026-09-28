#!/usr/bin/env node
// Test-Doppel fuer den Talk-Helfer.
//
// Bildet den Ablauf des echten Helfers nach, ohne Gateway oder Provider:
// Es meldet `ready`, dann eine abgeschlossene Nutzeraeusserung, danach
// Assistenten-Text in Stuecken und den Abschluss. Zusaetzlich schreibt es
// Rohaudio auf stdout, das die Bridge im lokalen Modus verwerfen muss.
//
// Aufruf wie der echte Helfer: node fake-talk-helper.mjs RATE WIDTH CHANNELS

const [, , rateText, widthText, channelsText] = process.argv;

function emit(type, data = {}) {
  process.stderr.write(`${JSON.stringify({ type, ...data })}\n`);
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function main() {
  emit("ready", { sessionId: "fake-session", consultSessionKey: null });
  await sleep(50);

  // Abgeschlossene Nutzeraeusserung: startet die lokale Sprachausgabe.
  emit("user_transcript", { text: "Wie ist der Stand?" });
  await sleep(30);

  // Assistenten-Text in Stuecken, wie der echte Helfer ihn liefert.
  const full = "Der Monitor ist an. Der Befehl ist an Alexa rausgegangen.";
  for (const part of ["Der Monitor ist an. ", "Der Befehl ist an Alexa rausgegangen."]) {
    emit("assistant_delta", { text: part });
    await sleep(20);
  }

  // Rohaudio auf stdout — im lokalen Modus muss die Bridge es verwerfen.
  const pcm = Buffer.alloc(24000 * 2 * 1); // 1 Sekunde Stille
  process.stdout.write(pcm);

  emit("assistant_done", {
    text: full,
    audioFrameCount: 1,
    audioByteCount: pcm.length,
    msSinceLastAudio: 5,
  });
  emit("audio_done", { audioFrameCount: 1, audioByteCount: pcm.length });

  // Am Leben bleiben, bis die Bridge den Prozess beendet.
  await sleep(120000);
}

main().catch((error) => {
  process.stderr.write(`${JSON.stringify({ type: "error", message: String(error) })}\n`);
});
