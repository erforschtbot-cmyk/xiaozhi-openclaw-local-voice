import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';

// Execute actual helper functions with fake I/O; never load GatewayClient.
const source = fs.readFileSync(new URL('./openclaw-talk-realtime.mjs', import.meta.url), 'utf8');
test('standby closes the owned relay before exiting and is idempotent', async () => {
  const calls=[];
  const context=vm.createContext({
    audioGateOpen:true, sessionId:'test', record:()=>{}, recordTurnFinished:()=>{},
    process:{stdin:{pause:()=>{}},exit:()=>calls.push('exit')},
    client:{request:async(method)=>calls.push(method),stopAndWait:async()=>calls.push('disconnect')},
  });
  vm.runInContext(source.slice(source.indexOf('let stopping ='),
    source.indexOf('process.once("SIGTERM"')),context);
  await vm.runInContext('stopOwnedSession()',context);
  await vm.runInContext('stopOwnedSession()',context);
  assert.deepEqual(calls,['talk.session.close','disconnect','exit']);
  assert.equal(context.audioGateOpen,false);
});
test('audio volume cannot hide errors or session completion', () => {
  const lines = [];
  const context = vm.createContext({
    fs: {appendFileSync: (_, line) => lines.push(JSON.parse(line))}, Date, Map,
  });
  vm.runInContext(`const FLIGHT_PATH='fake'; const FLIGHT_STARTED_AT=Date.now();
    let sessionId='test';
    ${source.slice(source.indexOf('let flightEvents'), source.indexOf('function gatewayToken'))}
    for(let i=0;i<20000;i++) {
      record('audio_accepted',{at:i*60});
      record('audio_flushed',{at:i*60});
      record('relay_event',{type:'inputAudio'});
    }
    record('relay_event',{type:'error',message:'failure'});
    record('turn_finished',{outcome:'closed'});
  `, context);
  assert.equal(lines.at(-1).stage, 'turn_finished');
  assert.equal(lines.at(-2).dtype, 'error');
  assert.ok(lines.length <= 402);
});

test('recovery preserves replay/live timestamp continuity', async () => {
  const calls=[];
  const context=vm.createContext({
    userFinal:false, relayRecoveries:0, bufferedInputMs:3000,
    sentIndex:20, timestamp:3000, sessionId:'test', record:()=>{}, emit:()=>{},
    audioGateOpen:true, flushing:null,
    client:{request:async(method)=>calls.push(method)},
    createRelaySession:async()=>calls.push('create'),
  });
  vm.runInContext(source.slice(source.indexOf('async function recoverRelayAndReplay'),
    source.indexOf('for await (const chunk of process.stdin)')),context);
  await vm.runInContext('recoverRelayAndReplay("test")',context);
  assert.equal(context.timestamp,3000);
  assert.equal(context.sentIndex,0);
  assert.deepEqual(calls,['talk.session.close','create']);
  assert.equal(context.audioGateOpen,false);
});

test('failed close cannot create an additional billed session', async () => {
  let created=0;
  const context=vm.createContext({
    userFinal:false, relayRecoveries:0, bufferedInputMs:3000,
    sentIndex:20, timestamp:3000, sessionId:'test', record:()=>{}, emit:()=>{},
    audioGateOpen:true, flushing:null,
    client:{request:async()=>{throw new Error('close timeout')}},
    createRelaySession:async()=>created++,
  });
  vm.runInContext(source.slice(source.indexOf('async function recoverRelayAndReplay'),
    source.indexOf('for await (const chunk of process.stdin)')),context);
  await assert.rejects(vm.runInContext('recoverRelayAndReplay("test")',context),/close timeout/);
  assert.equal(created,0);
  assert.equal(context.audioGateOpen,false);
});
