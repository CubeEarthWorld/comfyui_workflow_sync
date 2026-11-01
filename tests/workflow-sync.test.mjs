import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const source = readFileSync(new URL('../workflow-sync.js', import.meta.url), 'utf8')
  .replace(/^import .*;\r?\n/gm, '');
const clone = value => JSON.parse(JSON.stringify(value));
const workflow = id => ({nodes: [{id, type: 'Output'}]});

function screen(sharedStorage) {
  let graph = workflow(1);
  const saved = new Map();
  const published = [], warnings = [];
  const remote = {revision: 1, workflow: workflow(1), output: {}, seed_mode: 'saved'};
  const app = {
    graph: {serialize: () => clone(graph), _nodes: []},
    graphToPrompt: async () => ({workflow: clone(graph), output: {}}),
    loadGraphData: async value => {graph = clone(value);},
    loadApiJson: async () => {},
    registerExtension: () => {},
    extensionManager: {workflow: {}, toast: {add: value => warnings.push(value)}}
  };
  const context = vm.createContext({app, api: {
    clientId: 'browser',
    fetchApi: async (url, options = {}) => {
      if (url.endsWith('/lease')) return {ok: true, json: async () => ({ready: true})};
      if (options.method === 'POST') {
        const data = JSON.parse(options.body);
        if (data.revision !== remote.revision)
          return {ok: false, text: async () => 'Workflow changed elsewhere; keep your edits'};
        published.push(data);
        Object.assign(remote, data, {revision: remote.revision + 1});
      }
      return {ok: true, json: async () => clone(remote)};
    }
  }, localStorage: {getItem: key => (sharedStorage || saved).get(key),
    setItem: (key, value) => (sharedStorage || saved).set(key, value)},
  sessionStorage: {getItem: key => saved.get(key), setItem: (key, value) => saved.set(key, value)},
  console, setInterval: () => {}, queueMicrotask});
  vm.runInContext(source, context);
  vm.runInContext('booted = true; revision = 1; baseline = fingerprint(app.graph.serialize());', context);
  return {app, remote, saved, published, warnings, run: code => vm.runInContext(code, context),
    setGraph: value => {graph = clone(value);}, graph: () => clone(graph)};
}

test('a failed UI load cannot publish its partial graph on the next poll', async () => {
  const s = screen();
  s.remote.workflow = workflow(2);
  s.remote.revision = 2;
  s.app.loadGraphData = async () => {s.setGraph(workflow(99)); return false;};
  await s.run('sync()');
  await s.run('sync()');
  await s.run('sync()');
  assert.equal(s.published.length, 0);
  assert.deepEqual(s.remote.workflow, workflow(2));
  assert.deepEqual(JSON.parse(s.saved.get('ComfyCollaboration.beforeSync')), workflow(1));
});

test('a cancelled API load does not overwrite the remote graph', async () => {
  const s = screen();
  s.remote.workflow = null;
  s.remote.output = {'2': {class_type: 'Output', inputs: {}}};
  s.remote.revision = 2;
  s.app.loadApiJson = async () => false;
  await s.run('sync()');
  assert.equal(s.published.length, 0);
});

test('an export failure after loading retries instead of publishing a partial canvas', async () => {
  const s = screen();
  s.remote.workflow = workflow(2);
  s.remote.revision = 2;
  let loaded = false;
  s.app.loadGraphData = async value => {s.setGraph(value); loaded = true;};
  const exportGraph = s.app.graphToPrompt;
  s.app.graphToPrompt = async () => {
    if (loaded) {loaded = false; s.setGraph(workflow(99)); throw new Error('Export failed');}
    return exportGraph();
  };
  await s.run('sync()');
  await s.run('sync()');
  assert.equal(s.published.length, 0);
  assert.deepEqual(s.remote.workflow, workflow(2));
});

test('keep resolves later conflicts without changing the setting again', async () => {
  const s = screen();
  s.run("resolveMode = 'keep'");
  s.setGraph(workflow(3));
  s.remote.workflow = workflow(2);
  s.remote.revision = 2;
  await s.run('sync()');
  await s.run('sync()');
  assert.equal(s.published.length, 1);
  assert.deepEqual(s.remote.workflow, workflow(3));
});

test('restore uses the original backup when a later conflict occurs', async () => {
  const s = screen();
  s.saved.set('ComfyCollaboration.beforeSync', JSON.stringify(workflow(10)));
  s.run("resolveMode = 'restore'");
  s.setGraph(workflow(3));
  s.remote.revision = 2;
  await s.run('sync()');
  await s.run('sync()');
  assert.equal(s.published.length, 1);
  assert.deepEqual(s.remote.workflow, workflow(10));
});

test('ask leaves both sides intact until an explicit resolution', async () => {
  const s = screen();
  s.run("resolveMode = 'ask'");
  s.setGraph(workflow(3));
  s.remote.workflow = workflow(2);
  s.remote.revision = 2;
  await s.run('sync()');
  await s.run('sync()');
  assert.equal(s.published.length, 0);
  assert.deepEqual(s.graph(), workflow(3));
  await s.run("applyResolve(RESOLVE.keep.en)");
  assert.deepEqual(s.remote.workflow, workflow(3));
});

test('a resolution chosen during an in-flight poll runs on the next poll', async () => {
  const s = screen();
  s.run("busy = true; blocked = true; resolveMode = 'ask'");
  s.setGraph(workflow(3));
  s.remote.revision = 2;
  await s.run('applyResolve(RESOLVE.keep.en)');
  s.run('busy = false');
  await s.run('sync()');
  assert.deepEqual(s.remote.workflow, workflow(3));
});

test('the first pre-sync backup survives later loads and conflicts', async () => {
  const s = screen();
  s.run('revision = -1');
  s.remote.workflow = workflow(2);
  await s.run('sync()');
  s.setGraph(workflow(3));
  s.remote.workflow = workflow(4);
  s.remote.revision++;
  await s.run('sync()');
  await s.run('sync()');
  assert.deepEqual(s.remote.workflow, workflow(4));
  assert.deepEqual(JSON.parse(s.saved.get('ComfyCollaboration.beforeSync')), workflow(1));
});

test('a lease lost during publication does not discard local edits', async () => {
  const s = screen();
  s.setGraph(workflow(3));
  s.run("lease = async () => false");
  await s.run('sync()');
  assert.equal(s.published.length, 0);
  assert.deepEqual(s.graph(), workflow(3));
});

test('a second tab cannot replace the first tab backup', async () => {
  const sharedStorage = new Map();
  const first = screen(sharedStorage), second = screen(sharedStorage);
  first.run('revision = -1');
  first.remote.workflow = workflow(2);
  await first.run('sync()');
  second.setGraph(workflow(10));
  second.run('revision = -1');
  second.remote.workflow = workflow(20);
  await second.run('sync()');
  await first.run('applyResolve(RESOLVE.restore.en)');
  assert.deepEqual(first.graph(), workflow(1));
});

test('a failed restore cannot publish its partial canvas after changing the policy', async () => {
  const s = screen();
  s.saved.set('ComfyCollaboration.beforeSync', JSON.stringify(workflow(10)));
  s.app.loadGraphData = async () => {s.setGraph(workflow(99)); return false;};
  await s.run('applyResolve(RESOLVE.restore.en)');
  await s.run('applyResolve(RESOLVE.ask.en)');
  assert.equal(s.published.length, 0);
  assert.deepEqual(s.remote.workflow, workflow(1));
});

test('reloading a tab retains its existing session backup', async () => {
  const s = screen();
  s.saved.set('ComfyCollaboration.beforeSync', JSON.stringify(workflow(10)));
  s.run('revision = -1');
  await s.run('sync()');
  assert.deepEqual(JSON.parse(s.saved.get('ComfyCollaboration.beforeSync')), workflow(10));
});
