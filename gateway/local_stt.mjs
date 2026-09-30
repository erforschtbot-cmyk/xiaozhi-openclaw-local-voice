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
// Wachwort-Reste
// --------------------------------------------------------------------------
//
// Das Geraet oeffnet den Audiokanal erst, wenn es das Wachwort gehoert hat.
// Je nach Zeitpunkt liegen die ersten Rahmen noch davor, sodass Reste des
// Wachworts im Transkript landen. Whisper schreibt "Jarvis" sehr
// unterschiedlich; alle folgenden Formen wurden im echten Betrieb gesehen:
//
//   "Jarvis."   "Ja, das..."   "Job es."   "Ja, bis da an den Witz."
//
// Deshalb zwei Stufen:
//   1. Fuehrende Wachwort-Reste abschneiden (der Auftrag folgt dahinter).
//   2. Reine Wachwort-Reste ganz verwerfen.
const WAKE_LEAD =
  /^(?:hey[\s,]+)?(?:jarvis|jarwis|jawis|javis|dschawis|tschawis|charvis|harvis)\b[\s,.:;!?\-–]*/i;
const WAKE_ONLY =
  /^(?:hey[\s,]+)?(?:jarvis|jarwis|jawis|javis|dschawis|tschawis|charvis|harvis)[.!?]*$/i;
// Sehr kurze Fehldeutungen ohne eigenen Auftrag. Der Vergleich gilt nur fuer
// Aeusserungen mit hoechstens drei Woertern, damit ein echter Satz wie
// "Ja, das ist gut" nicht verschluckt wird.
const WAKE_SHORT = /^(?:ja,?\s*(?:das|bis)|job\s*es|jabis|japp?)$/i;

/**
 * Raeumt Wachwort-Reste aus einem Transkript.
 *
 * @param {string} text  Rohtranskript des lokalen Whisper
 * @returns {string} der verbleibende Auftrag, oder "" wenn nichts bleibt
 */
export function cleanTranscript(text) {
  const trimmed = (text || "").trim();
  if (!trimmed) return "";
  if (WAKE_ONLY.test(trimmed)) return "";
  const words = trimmed.replace(/[.!?]+$/, "").split(/\s+/).filter(Boolean);
  if (words.length <= 3 && WAKE_SHORT.test(words.join(" "))) return "";
  return trimmed.replace(WAKE_LEAD, "").trim();
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
 * Die absoluten Werte sind niedriger als man erwartet: Ein echtes Mikrofon
 * am Geraet liefert fuer Sprache RMS-Werte um 40-110 bei Stille um 15-20.
 * Voll ausgesteuertes Synthese-Audio (Piper) erreicht RMS um 1700 — eine
 * Kalibrierung an solchem Testmaterial setzt die Schwelle viel zu hoch.
 * Deshalb ist `speechRms` klein (35) und die Grundrauschen-Adaption
 * uebernimmt die Anpassung an den Raum.
 *
 * Die Ruheschwelle ist bewusst kurz (Standard 700 ms). Der frueher genutzte
 * OpenAI-Dienst wartete 3000 ms — diese Wartezeit faellt hier weg.
 */
export class UtteranceDetector {
  constructor(options = {}) {
    const {
      rate = 16000,
      speechRms = 30,
      silenceMs = 700,
      minSpeechMs = 200,
      maxUtteranceMs = 20000,
      onDiscard = null,
    } = options;
    this.rate = rate;
    this.speechRms = speechRms;
    this.silenceMs = silenceMs;
    this.minSpeechMs = minSpeechMs;
    this.maxUtteranceMs = maxUtteranceMs;
    this.onDiscard = onDiscard;
    // Startwert des Grundrauschens: gemessen liegt Stille am Geraet bei
    // RMS 14-20. Ein hoeherer Startwert wuerde die ersten Sekunden Sprache
    // unterdruecken, weil die Schwelle erst langsam absinkt.
    this.noiseFloorStart = 15;
    this.reset();
  }

  reset() {
    this.chunks = [];
    this.bytes = 0;
    this.speaking = false;
    this.speechMs = 0;
    this.silenceMsAccum = 0;
    this.noiseFloor = this.noiseFloorStart;
    this.startedAtMs = 0;
    this.lastRms = 0;
    this.rmsSamples = [];
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
    this.rmsSamples.push(rms);
    if (this.rmsSamples.length > 50) this.rmsSamples.shift();

    // Zwei Huerden: eine absolute Mindestschwelle und das Vielfache des
    // gelernten Grundrauschens. Der Faktor ist 1.6 statt 2: bei Sprache um
    // RMS 40-110 und Stille um 15-20 liegt das Doppelte des Grundrauschens
    // bereits auf Hoehe leiser Sprache und schnitt die ersten (leisen)
    // Silben weg. 1.6 laesst leise Sprache durch, ohne Raumbrummen zu greifen.
    const threshold = Math.max(this.speechRms, this.noiseFloor * 1.6);
    this.lastThreshold = threshold;
    const speakingNow = rms > threshold;

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
    // Eine Aeusserung endet, sobald genug STILLE war — unabhaengig davon, wie
    // viel Sprache erkannt wurde. Vorher verlangte die Bedingung zusaetzlich
    // speechMs >= minSpeechMs. Ein einzelner lauter Transient setzte damit
    // speaking=true, sammelte aber nur ~120 ms Sprache; die Endebedingung
    // griff nie, der Detektor sammelte bis maxUtteranceMs (20 s) und verwarf
    // dann STILL. In diesen 20 s war die Erkennung komplett blind — genau das
    // Symptom "Gerät hört zu, es kommt nichts, kein Request".
    const quietEnough = this.silenceMsAccum >= this.silenceMs;

    if (!tooLong && !quietEnough) return null;

    const hadSpeech = this.speechMs >= this.minSpeechMs;
    const stats = {
      speechMs: this.speechMs,
      silenceMs: this.silenceMsAccum,
      reason: tooLong ? "too-long" : "too-short",
    };
    const utterance = hadSpeech ? Buffer.concat(this.chunks) : null;
    this.reset();
    if (!hadSpeech) {
      // Sichtbar machen, statt still zu verwerfen: sonst faellt ein zu leiser
      // oder zu kurzer Aussetzer nie auf.
      if (this.onDiscard) this.onDiscard(stats);
      return null;
    }
    return utterance;
  }
}
