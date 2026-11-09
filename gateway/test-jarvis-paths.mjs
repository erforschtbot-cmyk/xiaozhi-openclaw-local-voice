// Prüft die konfigurierbaren Pfade und den ioBroker-Endpunkt.
//
// Aufruf: node gateway/test-jarvis-paths.mjs
// Es werden nur Umgebungsvariablen im eigenen Prozess gesetzt; nichts wird
// geschrieben und kein Netzwerkzugriff ausgelöst.
import assert from "node:assert/strict";
import os from "node:os";
import path from "node:path";

const { DATA_ROOT, SMARTHOME_DIR, dataFile, ioBrokerBase, alexaEndpoint } =
  await import("./jarvis-paths.mjs");

const home = os.homedir();
assert.equal(DATA_ROOT, path.join(home, ".local/share/jarvis-realtime-bridge"));
assert.equal(SMARTHOME_DIR, path.join(home, ".local/share/alexa-direct"));
assert.equal(dataFile("active-room.json"), path.join(DATA_ROOT, "active-room.json"));
assert.equal(ioBrokerBase(), "http://192.168.178.24:8087");
assert.equal(
  alexaEndpoint(),
  "http://192.168.178.24:8087/set/alexa2.0.Echo-Devices.G6G2MM12449402WK.Commands.textCommand",
);

process.env.JARVIS_DATA_ROOT = "/srv/jarvis/data";
process.env.JARVIS_SMARTHOME_DIR = "/srv/jarvis/smarthome";
process.env.JARVIS_IOBROKER_URL = "http://10.0.0.5:8087/";
process.env.JARVIS_ECHO_DEVICE_ID = "TESTECHO";

const reloaded = await import(`./jarvis-paths.mjs?override=${Date.now()}`);
assert.equal(reloaded.DATA_ROOT, "/srv/jarvis/data");
assert.equal(reloaded.SMARTHOME_DIR, "/srv/jarvis/smarthome");
assert.equal(reloaded.dataFile("last-utterance.json"), "/srv/jarvis/data/last-utterance.json");
assert.equal(reloaded.ioBrokerBase(), "http://10.0.0.5:8087");
assert.equal(
  reloaded.alexaEndpoint(),
  "http://10.0.0.5:8087/set/alexa2.0.Echo-Devices.TESTECHO.Commands.textCommand",
);

process.env.JARVIS_ALEXA_ENDPOINT =
  "http://192.168.1.10:8087/set/alexa2.0.Echo-Devices.ABC.Commands.textCommand?value=";
const explicit = await import(`./jarvis-paths.mjs?explicit=${Date.now()}`);
assert.equal(
  explicit.alexaEndpoint(),
  "http://192.168.1.10:8087/set/alexa2.0.Echo-Devices.ABC.Commands.textCommand",
);

console.log("jarvis-paths: alle Faelle bestanden");
