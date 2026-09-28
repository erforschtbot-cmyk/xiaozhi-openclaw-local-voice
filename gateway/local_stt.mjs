// Lokale Spracherkennung fuer die XiaoZhi-Bridge.
//
// Zwei Aufgaben, bewusst getrennt und einzeln testbar:
//
//   1. `transcribePcm()` schickt PCM an den lokalen Wyoming-Whisper-Dienst
//      (Standard: 127.0.0.1:10300) und liefert den Text zurueck.
//   2. `UtteranceDetector` erkennt, wann eine Aeusserung beginnt und endet.
//
// Warum eine eigene Pausend-Erkennung noetig ist
// ----------------------------------------------
// Vorher entschied der OpenAI-Realtime-Dienst per VAD, wann ein Satz zu Ende
// ist. Faellt dieser Dienst weg, muss die Bridge das selbst tun: sie sammelt
// Mikrofon-Audio, bis eine Sprechpause eintritt, und schickt erst dann den
// vollstaendigen Satz an Whisper. Whisper selbst erwartet ganze Aeusserungen,
// keine Einzelrahmen.

import net from "node:net";

const WYOMING_VERSION = "1.8.0";

// --------------------------------------------------------------------------
// Wyoming-Protokoll (Newline-JSON-Kopf, danach optionale Binaerfelder)
// --------------------------------------------------------------------------
//
// Format je Ereignis:
//   {"type": "...", "version": "...", "data_length": N, "payload_length": M}\n
//   <N Bytes UTF-8 JSON-Daten><M Bytes Nutzlast>
// Die Laengenfelder fehlen, wenn der jeweilige Teil leer ist.

function encodeEvent(type, data = null, payload = null) {
  const header = { type, version: WYOMING_VERSION };
  const parts = [];
  if (data) {
    const bytes = Buffer.from(JSON.stringify(data), "utf8");
    header.data_length = bytes.length;
    parts.push(bytes);
  }
  if (payload && payload.length) {
    header.payload_length = payload.length;
    parts.push(payload);
  }
  return Buffer.concat([
    Buffer.from(`${JSON.stringify(header)}\n`, "utf8"),
    ...parts,
  ]);
}

/** Liest Ereignisse aus einem Socket und ruft `onEvent` je Ereignis auf. */
function createReader(socket, onEvent) {
  let buffer = Buffer.alloc(0);
  socket.on("data", (chunk) => {
    buffer = Buffer.concat([buffer, chunk]);
    for (;;) {
      const newline = buffer.indexOf(0x0a);
      if (newline === -1) return;
      let header;
      try {
        header = JSON.parse(buffer.subarray(0, newline).toString("utf8"));
      } catch {
        buffer = buffer.subarray(newline + 1);
        continue;
      }
      const dataLength = header.data_length ?? 0;
      const payloadLength = header.payload_length ?? 0;
      const totalEnd = newline + 1 + dataLength + payloadLength;
      if (buffer.length < totalEnd) return;
      const dataBytes = buffer.subarray(newline + 1, newline + 1 + dataLength);
      const payload = buffer.subarray(
        newline + 1 + dataLength,
        totalEnd,
      );
      buffer = buffer.subarray(totalEnd);
      let data = {};
      if (dataLength > 0) {
        try {
          data = JSON.parse(dataBytes.toString("utf8"));
        } catch {
          data = {};
        }
      }
      onEvent({ type: header.type, data, payload });
    }
  });
}

// --------------------------------------------------------------------------
// Erkennung
// --------------------------------------------------------------------------

/**
 * Schickt PCM (s16le, mono) an den lokalen Whisper-Dienst.
 *
 * @param {Buffer} pcm        Rohaudio
 * @param {object} options
 * @param {string} options.uri         "127.0.0.1:10300"
 * @param {string} options.language    z. B. "de"
 * @param {number} options.rate        Abtastrate (Standard 16000)
 * @param {number} options.timeoutMs   Abbruch nach dieser Zeit
 * @returns {Promise<string>} erkannter Text (ggf. leer)
 */
export function transcribePcm(pcm, options = {}) {
  const {
    uri = "127.0.0.1:10300",
    language = "de",
    rate = 16000,
    width = 2,
    channels = 1,
    timeoutMs = 30000,
    chunkBytes = 8192,
  } = options;

  const [host, portText] = uri.split(":");
  const port = Number(portText);

  return new Promise((resolve, reject) => {
    const socket = net.createConnection({ host, port });
    let settled = false;
    let transcript = "";

    const finish = (error, text) => {
      if (settled) return;
      settled = true;
      socket.destroy();
      if (error) reject(error);
      else resolve(text);
    };

    const timer = setTimeout(
      () => finish(new Error("Whisper-Erkennung: Zeitueberschreitung")),
      timeoutMs,
    );

    socket.on("error", (error) => {
      clearTimeout(timer);
      finish(error);
    });

    createReader(socket, (event) => {
      if (event.type === "transcript") {
        transcript = (event.data.text ?? "").trim();
        clearTimeout(timer);
        finish(null, transcript);
      } else if (event.type === "error") {
        clearTimeout(timer);
        finish(new Error(event.data.text ?? "Whisper-Fehler"));
      }
    });

    socket.on("connect", () => {
      socket.write(encodeEvent("transcribe", { language }));
      socket.write(encodeEvent("audio-start", { rate, width, channels }));
      for (let offset = 0; offset < pcm.length; offset += chunkBytes) {
        const slice = pcm.subarray(offset, offset + chunkBytes);
        socket.write(
          encodeEvent("audio-chunk", { rate, width, channels }, slice),
        );
      }
      socket.write(encodeEvent("audio-stop", {}));
    });
  });
}

/**
 * Erkennt Aeusserungsgrenzen aus einem laufenden PCM-Strom.
 *
 * Verfahren: gleitender Effektivwert (RMS) je Rahmen. Liegt er deutlich ueber
 * dem gelernten Grundrauschen, gilt das als Sprache. Bleibt es danach lang
 * genug still, ist die Aeusserung beendet.
 *
 * Die Ruheschwelle ist bewusst kurz (Standard 700 ms). Der frueher genutzte
 * OpenAI-Dienst wartete 3000 ms — diese Wartezeit faellt hier weg.
 */
export class UtteranceDetector {
  constructor(options = {}) {
    const {
      rate = 16000,
      speechRms = 420,
      silenceMs = 700,
      minSpeechMs = 250,
      maxUtteranceMs = 20000,
    } = options;
    this.rate = rate;
    this.speechRms = speechRms;
    this.silenceMs = silenceMs;
    this.minSpeechMs = minSpeechMs;
    this.maxUtteranceMs = maxUtteranceMs;
    this.reset();
  }

  reset() {
    this.chunks = [];
    this.bytes = 0;
    this.speaking = false;
    this.speechMs = 0;
    this.silenceMsAccum = 0;
    this.noiseFloor = 60;
    this.startedAtMs = 0;
    this.lastRms = 0;
  }

  /** Effektivwert eines s16le-Rahmens. */
  static rms(buf) {
    const count = Math.floor(buf.length / 2);
    if (count === 0) return 0;
    let sum = 0;
    for (let index = 0; index < count; index += 1) {
      const sample = buf.readInt16LE(index * 2);
      sum += sample * sample;
    }
    return Math.sqrt(sum / count);
  }

  /**
   * Nimmt einen PCM-Rahmen auf.
   *
   * @returns {Buffer|null} die vollstaendige Aeusserung, sobald sie endet
   */
  push(buf) {
    if (!buf || buf.length < 2) return null;
    const durationMs = (buf.length / 2 / this.rate) * 1000;
    const rms = UtteranceDetector.rms(buf);
    this.lastRms = rms;

    const speakingNow =
      rms > Math.max(this.speechRms, this.noiseFloor * 3);

    if (!this.speaking) {
      if (!speakingNow) {
        // Grundrauschen nachfuehren, solange niemand spricht.
        this.noiseFloor = 0.95 * this.noiseFloor + 0.05 * rms;
        return null;
      }
      this.speaking = true;
      this.chunks = [];
      this.bytes = 0;
      this.speechMs = 0;
      this.silenceMsAccum = 0;
      this.startedAtMs = Date.now();
    }

    this.chunks.push(buf);
    this.bytes += buf.length;

    if (speakingNow) {
      this.speechMs += durationMs;
      this.silenceMsAccum = 0;
    } else {
      this.silenceMsAccum += durationMs;
    }

    const tooLong = Date.now() - this.startedAtMs > this.maxUtteranceMs;
    const quietEnough =
      this.silenceMsAccum >= this.silenceMs && this.speechMs >= this.minSpeechMs;

    if (!tooLong && !quietEnough) return null;

    const utterance = Buffer.concat(this.chunks);
    const hadSpeech = this.speechMs >= this.minSpeechMs;
    this.reset();
    return hadSpeech ? utterance : null;
  }
}
