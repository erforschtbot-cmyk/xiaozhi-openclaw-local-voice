import fs from "node:fs";
import { Type } from "typebox";
import { defineToolPlugin } from "openclaw/plugin-sdk/tool-plugin";
const ENDPOINT = "http://192.168.178.24:8087/set/alexa2.0.Echo-Devices.G6G2MM12449402WK.Commands.textCommand?value=";
// Raum-Voranstellung. Ohne Raum loest Alexa den Befehl in der Gruppe des
// angesprochenen Echos auf — und der ist fest das Arbeitszimmer. Deshalb
// bekommt "Licht an" den Raum des sprechenden Geraets vorangestellt.
const ACTIVE_ROOM = "/home/openclaw/.local/share/jarvis-realtime-bridge/active-room.json";
const ACTIVE_ROOM_TTL_MS = 300_000;
const TARGETS = new Set(["licht", "lichter", "steckdose", "steckdosen"]);
const CANONICAL = new Map([
    ["licht", "licht"], ["lichter", "licht"],
    ["steckdose", "steckdose"], ["steckdosen", "steckdose"],
]);
const ACTIONS = new Map([["an", "an"], ["ein", "an"], ["aus", "aus"]]);
const COMPOUND = /\b(?:und|oder|danach|dann|vorher|anschliessend|anschließend|spaeter|später|wenn|falls|bevor|nachdem|nicht|kein|keine|ausser|außer|aber|gleichzeitig|sekunden?|minuten?|stunden?|morgen|heute)\b/;
const LEAD = /^(?:bitte\s+)?(?:(?:schalte|schalt|mach|mache|stelle|stell)\s+)?(?:(?:den|die|das)\s+)?/;
const ROOM_WORDS = [
    "wohnzimmer", "arbeitszimmer", "schlafzimmer", "kinderzimmer", "badezimmer",
    "kueche", "küche", "flur", "diele", "bad", "buero", "büro", "keller",
    "garage", "garten", "terrasse", "balkon", "esszimmer", "gaestezimmer",
    "gästezimmer", "abstellraum", "hauswirtschaftsraum", "kino", "dachboden",
];
const ROOM_PREPOSITION = /\b(?:im|in der|in den|am|auf dem|auf der)\s+\S+/;
export function normalize(text) {
    return text.normalize("NFC").toLocaleLowerCase("de").trim().replace(/\s+/g, " ");
}
export function hasRoom(text) {
    const s = normalize(text);
    if (ROOM_PREPOSITION.test(s))
        return true;
    return ROOM_WORDS.some((room) => s.includes(room));
}
/** Raum des zuletzt sprechenden Geraets, oder null. Nie raten. */
export function readActiveRoom(now = Date.now(), path = ACTIVE_ROOM) {
    try {
        const data = JSON.parse(fs.readFileSync(path, "utf8"));
        const room = data?.room;
        let ts = Number(data?.ts);
        if (typeof room !== "string" || !room.trim())
            return null;
        if (!Number.isFinite(ts))
            return null;
        if (ts < 1e12)
            ts *= 1000;
        if (now - ts > ACTIVE_ROOM_TTL_MS || ts > now + 60_000)
            return null;
        return room.trim();
    }
    catch {
        return null;
    }
}
/** "Licht an" -> "Schlafzimmer Licht an". Sonst unveraendert. */
export function withRoom(text, roomPath = ACTIVE_ROOM, boundRoom) {
    const original = text.trim();
    const s = normalize(original).replace(/[.!?]+$/, "").trim();
    if (!s)
        return original;
    if (COMPOUND.test(s))
        return original;
    if (hasRoom(s))
        return original;
    if (original.endsWith("?"))
        return original;
    const parts = s.replace(LEAD, "").split(" ");
    if (parts.length !== 2)
        return original;
    const [target, action] = parts;
    if (!TARGETS.has(target) || !ACTIONS.has(action))
        return original;
    const room = boundRoom === undefined ? readActiveRoom(Date.now(), roomPath) : boundRoom;
    if (!room) {
        if (boundRoom === null)
            throw new Error("Kein eindeutig zugeordneter Raum. Bitte den Raum im Befehl nennen.");
        return original;
    }
    const cap = (w) => w.charAt(0).toLocaleUpperCase("de") + w.slice(1);
    const body = `${cap(room)} ${cap(CANONICAL.get(target))} ${ACTIONS.get(action)}`;
    const ending = /[.!?]$/.test(original) ? original.slice(-1) : "";
    return body + ending;
}
const parameters = Type.Object({
    text: Type.String({ minLength: 1, maxLength: 500, description: "Wörtlicher Befehl, z.B. Schalte den Monitor aus" }),
}, { additionalProperties: false });
async function executeAlexaCommand({ text }, _config, context) {
    if (!text.trim() || text.length > 500 || /[\x00-\x1f\x7f]/.test(text) || /^alexa\b/i.test(text.trim()))
        throw new Error("Ungültiger Text: 1–500 Zeichen, keine Steuerzeichen, kein Alexa-Präfix.");
    context.signal?.throwIfAborted();
    // Bound by the Gateway for this exact agent invocation. Never consult a
    // global last-device file during real execution (two rooms can overlap).
    const storage = globalThis[Symbol.for("jarvis.voiceTurn.v1")];
    const turn = storage?.getStore();
    const room = turn?.room && turn.observedAt && Date.now() - turn.observedAt < 120000 ? turn.room : null;
    const command = withRoom(text, ACTIVE_ROOM, room);
    const timeout = AbortSignal.timeout(10000);
    const signal = context.signal ? AbortSignal.any([context.signal, timeout]) : timeout;
    const response = await fetch(ENDPOINT + encodeURIComponent(command), { method: "GET", redirect: "error", signal });
    await response.body?.cancel();
    if (response.status !== 200)
        throw new Error(`ioBroker HTTP ${response.status}; nicht automatisch wiederholen.`);
    return { accepted: true, httpStatus: response.status, text: command, sentAs: command, physicalStateVerified: false };
}
export default defineToolPlugin({
    id: "iobroker-alexa",
    name: "ioBroker Alexa",
    description: "Sendet Smart-Home-Sprachbefehle an den festen Alexa-ioBroker-Datenpunkt. Zustandsändernder Aufruf.",
    tools: (tool) => [tool({
            name: "iobroker_alexa_send",
            description: "Sendet einen wörtlichen Smart-Home-Befehl ohne Alexa-Präfix an Echo Arbeitszimmer. Verändert Geräte über Alexa; kein Lesewerkzeug. HTTP-Erfolg bedeutet angenommen, nicht physisch ausgeführt. Keine automatischen Wiederholungen bei Fehlern.",
            parameters,
            execute: executeAlexaCommand,
        }), tool({
            name: "iobroker_alexa",
            description: "Führt einen wörtlichen Smart-Home-Befehl ohne Alexa-Präfix über Echo Arbeitszimmer aus. Für direkte Sprachbefehle ohne zusätzliche Bestätigungsfrage. HTTP-Erfolg bedeutet angenommen, nicht physisch verifiziert. Keine automatischen Wiederholungen.",
            parameters,
            execute: executeAlexaCommand,
        })],
});
