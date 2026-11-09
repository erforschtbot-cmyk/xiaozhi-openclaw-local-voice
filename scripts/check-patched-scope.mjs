#!/usr/bin/env node
/**
 * Prueft gepatchte OpenClaw-Bundles auf undefinierte Bezeichner.
 *
 * Warum: Ein Patch kann syntaktisch gueltig sein, alle Marker tragen und alle
 * Router-Tests bestehen — und trotzdem eine Variable benutzen, die im
 * Bundle-Scope nie deklariert wurde. Genau das ist am 2026-10-20 passiert:
 * `jarvisObservedTurn is not defined` im onTranscript-Callback beendete den
 * gesamten Gateway-Prozess, sobald der erste finale Satz eintraf. `node
 * --check` und die Marker-Pruefung koennen das nicht sehen, weil der Fehler
 * erst zur Laufzeit in einem Pfad auftritt, den die Unit-Tests nicht
 * ausfuehren.
 *
 * Aufruf:
 *   node scripts/check-patched-scope.mjs <datei.mjs> [...]
 *   node scripts/check-patched-scope.mjs --dist <dist-verzeichnis>
 *
 * Exit 0 = jeder Bezeichner ist aufloesbar. Exit 1 = Befund.
 */
import { readFileSync, readdirSync } from 'node:fs';
import { createRequire } from 'node:module';
import { execFileSync } from 'node:child_process';
import path from 'node:path';

const NPM_ROOT = execFileSync('npm', ['root', '-g'], { encoding: 'utf8' }).trim();
const require = createRequire(path.join(NPM_ROOT, 'openclaw', 'package.json'));
const acorn = require('acorn');

const GLOBALS = new Set([
  'globalThis', 'console', 'process', 'Buffer', 'URL', 'URLSearchParams', 'URLPattern',
  'setTimeout', 'clearTimeout', 'setInterval', 'clearInterval', 'setImmediate', 'clearImmediate',
  'queueMicrotask', 'Promise', 'AggregateError', 'Map', 'Set', 'WeakMap', 'WeakSet', 'WeakRef',
  'FinalizationRegistry', 'Symbol', 'Error', 'TypeError', 'RangeError', 'ReferenceError',
  'SyntaxError', 'EvalError', 'URIError', 'Object', 'Array', 'String', 'Number', 'Boolean',
  'Date', 'JSON', 'Math', 'RegExp', 'Intl', 'TextEncoder', 'TextDecoder', 'TextEncoderStream',
  'TextDecoderStream', 'AbortController', 'AbortSignal', 'structuredClone', 'performance',
  'fetch', 'Headers', 'Request', 'Response', 'FormData', 'Blob', 'File', 'crypto',
  'atob', 'btoa', 'undefined', 'NaN', 'Infinity', 'arguments', 'require', 'module', 'exports',
  '__dirname', '__filename', 'Uint8Array', 'Uint8ClampedArray', 'Uint16Array', 'Uint32Array',
  'Int8Array', 'Int16Array', 'Int32Array', 'Float32Array', 'Float64Array', 'ArrayBuffer',
  'SharedArrayBuffer', 'DataView', 'BigInt', 'BigInt64Array', 'BigUint64Array', 'Proxy',
  'Reflect', 'Iterator', 'AsyncIterator', 'EventTarget', 'Event', 'MessageChannel',
  'MessagePort', 'BroadcastChannel', 'Worker', 'SharedWorker', 'navigator', 'location',
  'WebSocket', 'EventSource', 'ReadableStream', 'WritableStream', 'TransformStream',
  'CompressionStream', 'DecompressionStream', 'DOMException', 'atob', 'btoa',
]);

const files = (() => {
  const args = process.argv.slice(2);
  if (args[0] === '--dist') {
    const dir = args[1];
    if (!dir) {
      console.error('--dist braucht ein Verzeichnis');
      process.exit(2);
    }
    return readdirSync(dir)
      .filter((n) => /^handlers-.*\.mjs$/.test(n))
      .map((n) => path.join(dir, n));
  }
  return args;
})();

if (files.length === 0) {
  console.error('Keine Eingabedatei. Siehe Kopf dieser Datei.');
  process.exit(2);
}

class Scope {
  constructor(parent) {
    this.parent = parent;
    this.names = new Set();
  }
  declare(name) {
    this.names.add(name);
  }
  has(name) {
    for (let s = this; s; s = s.parent) if (s.names.has(name)) return true;
    return false;
  }
}

function bindPattern(node, declare) {
  if (!node) return;
  switch (node.type) {
    case 'Identifier':
      declare(node.name);
      break;
    case 'ObjectPattern':
      for (const p of node.properties) {
        bindPattern(p.type === 'RestElement' ? p.argument : p.value, declare);
      }
      break;
    case 'ArrayPattern':
      for (const el of node.elements) bindPattern(el, declare);
      break;
    case 'AssignmentPattern':
      bindPattern(node.left, declare);
      break;
    case 'RestElement':
      bindPattern(node.argument, declare);
      break;
    default:
      break;
  }
}

/** `var` und Funktionsdeklarationen gelten im ganzen Funktionskoerper. */
function hoistVars(node, scope) {
  if (!node || typeof node.type !== 'string') return;
  if (node.type === 'VariableDeclaration' && node.kind === 'var') {
    for (const d of node.declarations) bindPattern(d.id, (n) => scope.declare(n));
  }
  if (node.type === 'FunctionDeclaration' && node.id) scope.declare(node.id.name);
  if (
    node.type === 'FunctionExpression' ||
    node.type === 'ArrowFunctionExpression' ||
    node.type === 'ClassExpression' ||
    node.type === 'ClassDeclaration'
  ) {
    return;
  }
  for (const key of Object.keys(node)) {
    const child = node[key];
    if (Array.isArray(child)) child.forEach((c) => hoistVars(c, scope));
    else if (child && typeof child.type === 'string') hoistVars(child, scope);
  }
}

/** let/const/class gelten im ganzen Block, auch vor ihrer Zeile (TDZ). */
function hoistLexical(statements, scope) {
  for (const st of statements) {
    if (!st || typeof st.type !== 'string') continue;
    if (st.type === 'VariableDeclaration' && st.kind !== 'var') {
      for (const d of st.declarations) bindPattern(d.id, (n) => scope.declare(n));
    } else if (st.type === 'ClassDeclaration' && st.id) {
      scope.declare(st.id.name);
    } else if (st.type === 'FunctionDeclaration' && st.id) {
      scope.declare(st.id.name);
    } else if (st.type === 'ExportNamedDeclaration' && st.declaration) {
      hoistLexical([st.declaration], scope);
    } else if (st.type === 'ExportDefaultDeclaration' && st.declaration?.id) {
      scope.declare(st.declaration.id.name);
    }
  }
}

function collect(ast) {
  const unresolved = [];

  const walk = (node, scope) => {
    if (!node || typeof node.type !== 'string') return;

    switch (node.type) {
      case 'Program': {
        const s = new Scope(null);
        hoistVars(node, s);
        hoistLexical(node.body, s);
        for (const st of node.body) walk(st, s);
        return;
      }
      case 'ImportDeclaration':
        for (const spec of node.specifiers) scope.declare(spec.local.name);
        return;
      case 'FunctionDeclaration':
      case 'FunctionExpression':
      case 'ArrowFunctionExpression': {
        const s = new Scope(scope);
        for (const p of node.params) bindPattern(p, (n) => s.declare(n));
        if (node.type === 'FunctionExpression' && node.id) s.declare(node.id.name);
        if (node.body.type === 'BlockStatement') {
          hoistVars(node.body, s);
          hoistLexical(node.body.body, s);
          for (const st of node.body.body) walk(st, s);
        } else {
          walk(node.body, s);
        }
        return;
      }
      case 'BlockStatement': {
        const s = new Scope(scope);
        hoistLexical(node.body, s);
        for (const st of node.body) walk(st, s);
        return;
      }
      case 'ForStatement':
      case 'ForInStatement':
      case 'ForOfStatement': {
        const s = new Scope(scope);
        for (const part of [node.init, node.left]) {
          if (part?.type === 'VariableDeclaration' && part.kind !== 'var') {
            for (const d of part.declarations) bindPattern(d.id, (n) => s.declare(n));
          }
        }
        for (const key of ['init', 'test', 'update', 'left', 'right', 'body']) {
          if (node[key]) walk(node[key], s);
        }
        return;
      }
      case 'CatchClause': {
        const s = new Scope(scope);
        if (node.param) bindPattern(node.param, (n) => s.declare(n));
        walk(node.body, s);
        return;
      }
      case 'ClassDeclaration':
        if (node.id) scope.declare(node.id.name);
        if (node.superClass) walk(node.superClass, scope);
        walk(node.body, new Scope(scope));
        return;
      case 'VariableDeclaration':
        if (node.kind !== 'var') {
          for (const d of node.declarations) bindPattern(d.id, (n) => scope.declare(n));
        }
        for (const d of node.declarations) {
          if (d.id.type !== 'Identifier') walk(d.id, scope);
          walk(d.init, scope);
        }
        return;
      case 'Identifier':
        if (!scope.has(node.name) && !GLOBALS.has(node.name)) {
          unresolved.push({
            name: node.name,
            line: node.loc.start.line,
            column: node.loc.start.column + 1,
          });
        }
        return;
      case 'MemberExpression':
        walk(node.object, scope);
        if (node.computed) walk(node.property, scope);
        return;
      case 'Property':
      case 'MethodDefinition':
      case 'PropertyDefinition':
        if (node.computed) walk(node.key, scope);
        walk(node.value, scope);
        return;
      case 'LabeledStatement':
        walk(node.body, scope);
        return;
      case 'BreakStatement':
      case 'ContinueStatement':
      case 'ExportAllDeclaration':
      case 'MetaProperty':
        return;
      case 'ExportNamedDeclaration':
        if (node.declaration) walk(node.declaration, scope);
        for (const spec of node.specifiers ?? []) walk(spec.local, scope);
        return;
      case 'ExportDefaultDeclaration':
        walk(node.declaration, scope);
        return;
      default:
        for (const key of Object.keys(node)) {
          if (key === 'loc' || key === 'range' || key === 'start' || key === 'end') continue;
          const child = node[key];
          if (Array.isArray(child)) child.forEach((c) => walk(c, scope));
          else if (child && typeof child.type === 'string') walk(child, scope);
        }
    }
  };

  walk(ast, null);
  return unresolved;
}

let failed = false;
for (const file of files) {
  const source = readFileSync(file, 'utf8');
  let ast;
  try {
    ast = acorn.parse(source, {
      ecmaVersion: 'latest',
      sourceType: 'module',
      locations: true,
      allowHashBang: true,
    });
  } catch (error) {
    console.error(`SCOPE_PARSE_FAILED: ${file}: ${error.message}`);
    failed = true;
    continue;
  }
  const found = collect(ast);
  if (found.length === 0) {
    console.log(`SCOPE_OK: ${file}`);
    continue;
  }
  failed = true;
  console.error(`SCOPE_UNDEFINED: ${file}`);
  const seen = new Set();
  for (const hit of found) {
    const key = `${hit.name}:${hit.line}`;
    if (seen.has(key)) continue;
    seen.add(key);
    console.error(`  ${hit.name} @ ${hit.line}:${hit.column}`);
  }
}

process.exit(failed ? 1 : 0);
