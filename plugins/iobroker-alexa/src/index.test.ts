import { afterEach, expect, it, vi } from "vitest";
import { AsyncLocalStorage } from "node:async_hooks";
import entry from "./index.js";
import { getToolPluginMetadata } from "openclaw/plugin-sdk/tool-plugin";
const tools = getToolPluginMetadata(entry)!.tools as any[];
afterEach(() => vi.unstubAllGlobals());
it("declares the legacy tool and the voice-safe alias", () => {
  expect(tools.map((tool) => tool.name)).toEqual(["iobroker_alexa_send", "iobroker_alexa"]);
});
const definitions: any[] = [];
(entry as any).register({ registerTool: (t: any) => definitions.push(t) });
const execute = (text: string, index = 1) => definitions[index].execute("test", { text });
it("isolates concurrent device rooms across async tool execution", async () => {
  const key = Symbol.for("jarvis.voiceTurn.v1");
  const globals = globalThis as Record<symbol, unknown>;
  const previous = globals[key];
  const storage = new AsyncLocalStorage();
  globals[key] = storage;
  const fetch = vi.fn().mockImplementation(async () => new Response("OK", { status: 200 }));
  vi.stubGlobal("fetch", fetch);
  try {
    await Promise.all(["Schlafzimmer", "Bad"].map(room => storage.run({ room, observedAt: Date.now() }, async () => {
      await Promise.resolve();
      await execute("Licht an");
    })));
    expect(fetch.mock.calls.map(c => new URL(c[0]).searchParams.get("value")).sort()).toEqual(["Bad Licht an", "Schlafzimmer Licht an"]);
  } finally { globals[key] = previous; }
});
it("refuses generic light command without bound room before HTTP", async () => {
  const fetch = vi.fn(); vi.stubGlobal("fetch", fetch);
  await expect(execute("Licht an")).rejects.toThrow("Raum");
  expect(fetch).not.toHaveBeenCalled();
});
it("encodes text, uses fixed endpoint and refuses redirects", async () => {
  const fetch = vi.fn().mockResolvedValue(new Response("OK", { status: 200 })); vi.stubGlobal("fetch", fetch);
  await execute("Licht Küche & Büro aus");
  expect(fetch).toHaveBeenCalledOnce();
  expect(fetch.mock.calls[0][0]).toBe("http://192.168.178.24:8087/set/alexa2.0.Echo-Devices.G6G2MM12449402WK.Commands.textCommand?value=" + encodeURIComponent("Licht Küche & Büro aus"));
  expect(fetch.mock.calls[0][1].redirect).toBe("error");
});
it("rejects invalid input before HTTP", async () => {
  const fetch = vi.fn(); vi.stubGlobal("fetch", fetch);
  await expect(execute(" ")).rejects.toThrow(); expect(fetch).not.toHaveBeenCalled();
});
it("reports HTTP errors without retry", async () => {
  const fetch = vi.fn().mockResolvedValue(new Response("error", { status: 500 })); vi.stubGlobal("fetch", fetch);
  await expect(execute("Monitor aus")).rejects.toThrow("500"); expect(fetch).toHaveBeenCalledOnce();
});

// --- Raum-Voranstellung -----------------------------------------------------
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { withRoom } from "./index.js";

const makeRoomFile = (room: string | null, ageSeconds = 0) => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "room-"));
  const file = path.join(dir, "active-room.json");
  if (room) fs.writeFileSync(file, JSON.stringify({ room, ts: Math.floor(Date.now() / 1000) - ageSeconds }));
  return file;
};

it("stellt den Raum vor Licht und Steckdose", () => {
  const file = makeRoomFile("Schlafzimmer");
  expect(withRoom("Licht an", file)).toBe("Schlafzimmer Licht an");
  expect(withRoom("licht aus", file)).toBe("Schlafzimmer Licht aus");
  expect(withRoom("Schalte das Licht an", file)).toBe("Schlafzimmer Licht an");
  expect(withRoom("Steckdose an", file)).toBe("Schlafzimmer Steckdose an");
});

it("normalisiert Plural auf Einzahl", () => {
  const file = makeRoomFile("Bad");
  expect(withRoom("Lichter an", file)).toBe("Bad Licht an");
  expect(withRoom("Steckdosen aus", file)).toBe("Bad Steckdose aus");
});

it("laesst alles andere unveraendert", () => {
  const file = makeRoomFile("Schlafzimmer");
  for (const text of [
    "Monitor aus",
    "Wegbeleuchtung an",
    "Licht im Wohnzimmer an",
    "Licht an und Steckdose aus",
    "Ist das Licht an?",
    "Licht 50 %",
    "Erzähle einen Witz",
  ]) {
    expect(withRoom(text, file), text).toBe(text);
  }
});

it("ohne oder mit abgelaufenem Raum keine Umstellung", () => {
  expect(withRoom("Licht an", makeRoomFile(null))).toBe("Licht an");
  expect(withRoom("Licht an", makeRoomFile("Schlafzimmer", 400))).toBe("Licht an");
});
