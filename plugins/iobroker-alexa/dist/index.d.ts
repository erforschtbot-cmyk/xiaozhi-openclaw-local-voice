export declare function normalize(text: string): string;
export declare function hasRoom(text: string): boolean;
/** Raum des zuletzt sprechenden Geraets, oder null. Nie raten. */
export declare function readActiveRoom(now?: number, path?: string): string | null;
/** "Licht an" -> "Schlafzimmer Licht an". Sonst unveraendert. */
export declare function withRoom(text: string, roomPath?: string, boundRoom?: string | null): string;
declare const _default: import("openclaw/plugin-sdk/tool-plugin").DefinedToolPluginEntry;
export default _default;
