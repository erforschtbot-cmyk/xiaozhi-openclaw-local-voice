// Zentrale Pfad- und Endpunktauflösung für alle Jarvis-Module.
//
// Ohne gesetzte Umgebungsvariablen gilt exakt der Referenzort
// /home/openclaw/.local/share/... . Wer in einem Container oder unter einem
// anderen Benutzer installiert, setzt JARVIS_DATA_ROOT und
// JARVIS_SMARTHOME_DIR – ohne Codeänderung.
//
// Der ioBroker-Endpunkt wird in dieser Reihenfolge aufgelöst:
//   1. JARVIS_ALEXA_ENDPOINT (komplette URL, optional mit ?value=)
//   2. settings.json der Oberfläche (iobroker_url)
//   3. JARVIS_IOBROKER_URL / IOBROKER_SIMPLE_API
//   4. Referenzadresse (nur Rückfall, nie stillschweigend für fremde Netze)
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

export const DATA_ROOT =
  process.env.JARVIS_DATA_ROOT ||
  path.join(os.homedir(), ".local/share/jarvis-realtime-bridge");
export const SMARTHOME_DIR =
  process.env.JARVIS_SMARTHOME_DIR ||
  path.join(os.homedir(), ".local/share/alexa-direct");

export function dataFile(name) {
  return path.join(DATA_ROOT, name);
}

const REFERENCE_IOBROKER = "http://192.168.178.24:8087";
const REFERENCE_ECHO = "G6G2MM12449402WK";

function settingsIoBroker() {
  try {
    const data = JSON.parse(
      fs.readFileSync(path.join(SMARTHOME_DIR, "settings.json"), "utf8"),
    );
    const value = data?.iobroker_url;
    if (typeof value === "string" && value.trim()) {
      return value.trim().replace(/\/+$/, "");
    }
  } catch {
    // keine oder unlesbare settings.json: nächste Quelle
  }
  return null;
}

/** Basisadresse des ioBroker Simple API, ohne abschließenden Slash. */
export function ioBrokerBase() {
  return (
    settingsIoBroker() ||
    process.env.JARVIS_IOBROKER_URL ||
    process.env.IOBROKER_SIMPLE_API ||
    REFERENCE_IOBROKER
  ).replace(/\/+$/, "");
}

/** Vollständige textCommand-URL ohne „?value=“. */
export function alexaEndpoint() {
  const explicit = process.env.JARVIS_ALEXA_ENDPOINT;
  if (typeof explicit === "string" && explicit.trim()) {
    return explicit.trim().replace(/\?value=$/, "").replace(/\/+$/, "");
  }
  const echo = process.env.JARVIS_ECHO_DEVICE_ID || REFERENCE_ECHO;
  return `${ioBrokerBase()}/set/alexa2.0.Echo-Devices.${echo}.Commands.textCommand`;
}

/** Nur für Diagnose: sagt, ob der Endpunkt aus einer echten Konfiguration kommt. */
export function alexaEndpointIsConfigured() {
  return Boolean(
    process.env.JARVIS_ALEXA_ENDPOINT ||
      settingsIoBroker() ||
      process.env.JARVIS_IOBROKER_URL ||
      process.env.IOBROKER_SIMPLE_API ||
      process.env.JARVIS_ECHO_DEVICE_ID,
  );
}
