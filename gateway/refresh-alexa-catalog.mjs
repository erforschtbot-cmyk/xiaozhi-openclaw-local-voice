import fs from 'node:fs/promises';
import { buildCatalog, CATALOG } from './smart-home-router.mjs';

const base = 'http://192.168.178.24:8087';
async function read(url) {
  const response = await fetch(url, { signal: AbortSignal.timeout(6000), redirect: 'error' });
  if (!response.ok) throw new Error(`catalog HTTP ${response.status}`);
  return response.json();
}
const objects = await read(`${base}/objects?pattern=alexa2.0.Smart-Home-Devices.*`);
const ids = Object.keys(objects).filter(k => k.endsWith('.powerState') && objects[k].common?.write === true).map(k => k.slice(0, -11));
if (!ids.length) throw new Error('Empty Alexa catalog; retaining previous snapshot');
const devices = [];
// Bounded, read-only refresh outside the voice turn. All-or-nothing: if any
// lookup fails, retain the old catalog, which expires after 48 hours.
for (const id of ids) {
  const obj = await read(`${base}/get/${encodeURIComponent(id)}`);
  const name = obj.native?.friendlyName ?? obj.common?.name;
  if (typeof name !== 'string') throw new Error(`Missing name: ${id}`);
  devices.push({ id, name, power: true });
}
const catalog = buildCatalog(devices);
const temporary = `${CATALOG}.${process.pid}.tmp`;
await fs.writeFile(temporary, JSON.stringify(catalog, null, 2) + '\n', { mode: 0o600, flag: 'wx' });
await fs.rename(temporary, CATALOG);
console.log(JSON.stringify({ discovered: devices.length, admitted: catalog.entries.length, names: catalog.entries.map(d => d.name) }));
