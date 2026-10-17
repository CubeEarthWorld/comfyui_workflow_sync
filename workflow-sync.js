import { app } from '../../scripts/app.js';
import { api } from '../../scripts/api.js';

/* ------------------------------------------------------------------ *
 * Localization
 *
 * The UI follows the ComfyUI language setting (Comfy.Locale) and falls
 * back to the browser language. Translations live in this file and are
 * mirrored in ./locales/*.json for translators.
 * ------------------------------------------------------------------ */
const LANGS = ['en', 'ja', 'zh', 'ko'];

const I18N = {
  en: {
    settingSync: 'Sync the workflow with the LLM',
    settingAutoOpen: 'Auto-open the workflow the LLM edited',
    settingResolve: 'On sync conflict',
    syncName: 'Workflow sync',
    errLoad: 'Could not load the workflow. The shared data was not overwritten.',
    errMismatch: 'The loaded graph does not match. The shared data was not overwritten.',
    errBusy: 'Another ComfyUI screen is editing. Close it and this screen can sync; your edits are kept.',
    errGenFailed: 'Generation failed. See the job history for details.',
    errNoRestore: 'There is no workflow to restore.',
    okOpened: 'Opened the LLM workflow',
    okOpenedDetail: 'The workflow the LLM edited was applied to this screen.'
  },
  ja: {
    settingSync: 'LLMとのワークフロー同期',
    settingAutoOpen: 'LLMが操作したワークフローを自動で開く',
    settingResolve: '同期が競合したときの操作',
    syncName: 'ワークフローの同期',
    errLoad: 'ワークフローを読み込めませんでした。共有データは上書きしていません。',
    errMismatch: '読み込み後のグラフが一致しません。共有データは上書きしていません。',
    errBusy: '別のComfyUI画面が編集中です。そちらを閉じるとこの画面から同期できます。変更内容は保持しています。',
    errGenFailed: '生成に失敗しました。ジョブ履歴で詳細を確認できます。',
    errNoRestore: '復元できるワークフローがありません',
    okOpened: 'LLMのワークフローを開きました',
    okOpenedDetail: '操作したワークフローをこの画面に反映しました。'
  },
  zh: {
    settingSync: '与 LLM 同步工作流',
    settingAutoOpen: '自动打开 LLM 编辑的工作流',
    settingResolve: '同步冲突时的操作',
    syncName: '工作流同步',
    errLoad: '无法加载工作流。未覆盖共享数据。',
    errMismatch: '加载后的图不一致。未覆盖共享数据。',
    errBusy: '另一个 ComfyUI 画面正在编辑。关闭它后本画面即可同步；你的修改会保留。',
    errGenFailed: '生成失败。请在任务历史中查看详情。',
    errNoRestore: '没有可恢复的工作流。',
    okOpened: '已打开 LLM 的工作流',
    okOpenedDetail: '已将 LLM 编辑的工作流应用到本画面。'
  },
  ko: {
    settingSync: 'LLM과 워크플로 동기화',
    settingAutoOpen: 'LLM이 편집한 워크플로 자동 열기',
    settingResolve: '동기화 충돌 시 동작',
    syncName: '워크플로 동기화',
    errLoad: '워크플로를 불러오지 못했습니다. 공유 데이터는 덮어쓰지 않았습니다.',
    errMismatch: '불러온 그래프가 일치하지 않습니다. 공유 데이터는 덮어쓰지 않았습니다.',
    errBusy: '다른 ComfyUI 화면이 편집 중입니다. 닫으면 이 화면에서 동기화할 수 있습니다. 변경 내용은 유지됩니다.',
    errGenFailed: '생성에 실패했습니다. 작업 기록에서 자세히 확인하세요.',
    errNoRestore: '복원할 워크플로가 없습니다.',
    okOpened: 'LLM 워크플로를 열었습니다',
    okOpenedDetail: 'LLM이 편집한 워크플로를 이 화면에 반영했습니다.'
  }
};

let LANG = 'en';
let M = I18N.en;

function detectLang() {
  let raw = '';
  try { raw = app.ui?.settings?.getSettingValue?.('Comfy.Locale') || ''; } catch (e) { /* not ready */ }
  if (!raw && typeof navigator !== 'undefined') raw = navigator.language || '';
  const code = String(raw).toLowerCase().split(/[-_]/)[0];
  return LANGS.includes(code) ? code : 'en';
}

/* Conflict resolution options. ComfyUI stores the visible label as the
 * setting value, so every translation of a label maps back to a stable key.
 * The default is to load the LLM changes. */
const RESOLVE = {
  ask:     { en: 'Ask me',              ja: '選択してください',     zh: '请选择',          ko: '선택하세요' },
  keep:    { en: 'Keep my edits',       ja: '画面の編集を採用',     zh: '采用画面编辑',     ko: '화면 편집 채택' },
  load:    { en: 'Load the LLM changes', ja: 'LLMの変更を読み込む', zh: '载入 LLM 的更改',  ko: 'LLM 변경 불러오기' },
  restore: { en: 'Restore before sync', ja: '同期前の画面を復元',   zh: '恢复同步前画面',   ko: '동기화 이전 복원' }
};
const RESOLVE_KEYS = ['ask', 'keep', 'load', 'restore'];
const resolveOptions = lang => RESOLVE_KEYS.map(key => RESOLVE[key][lang]);
function resolveKey(value) {
  for (const lang of LANGS) {
    const key = RESOLVE_KEYS.find(k => RESOLVE[k][lang] === value);
    if (key) return key;
  }
  return 'load';
}

/* ------------------------------------------------------------------ *
 * State
 * ------------------------------------------------------------------ */
let revision = -1, baseline = '', busy = false, loading = false, booted = false;
let enabled = true, blocked = false, lastError = '', forcePublish = false;
let autoOpen = true, openRequest = null, resolveMode = 'load';
const mirrored = new Map();

function fingerprint(workflow) {
  // Viewport navigation and Comfy's local revision are not workflow edits.
  const {revision: localRevision, extra = {}, ...graph} = workflow;
  const {ds: viewport, ...metadata} = extra;
  return JSON.stringify({...graph, extra: metadata});
}
async function request(path, data) {
  const response = await api.fetchApi('/workflow-sync/' + path, data === undefined ? {} : {
    method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data)
  });
  if (!response.ok) throw new Error(await response.text());
  return response.json();
}
function warn(error) {
  if (lastError === error.message) return;
  lastError = error.message;
  app.extensionManager.toast.add({severity: 'warn', summary: M.syncName, detail: error.message, life: 7000});
}
async function lease(editing = false) {
  return api.clientId && (await request('lease', {client_id: api.clientId, revision, editing, protocol: 1, auto_open: autoOpen})).ready;
}
async function load(workflow) {
  if (Array.isArray(workflow.nodes)) {
    const active = app.extensionManager.workflow.activeWorkflow;
    const applied = await app.loadGraphData(workflow, true, true, active);
    if (applied === false) throw new Error(M.errLoad);
    const expected = workflow.nodes.map(node => String(node.id)).sort();
    const actual = app.graph.serialize().nodes.map(node => String(node.id)).sort();
    if (JSON.stringify(expected) !== JSON.stringify(actual))
      throw new Error(M.errMismatch);
  } else await app.loadApiJson(workflow, 'Workflow collaboration.json');
}
function seedControls(output, mode) {
  const seed_modes = {}, seed_widgets = {}, stack = [[app.graph, []]], seen = new Set();
  const promoted = [];
  while (stack.length) {
    const [graph, parent] = stack.pop();
    if (seen.has(graph)) continue;
    seen.add(graph);
    for (const node of graph._nodes || []) {
      const path = [...parent, String(node.id)], widgets = node.widgets || [];
      if (node.isSubgraphNode?.() && node.subgraph) stack.push([node.subgraph, path]);
      let index = 0;
      for (const widget of widgets) {
        const controls = [widget, ...(widget.linkedWidgets || [])];
        for (const control of controls)
          if (control.name === 'control_after_generate' && ['fixed', 'randomize'].includes(mode)) control.value = mode;
        const address = path.join(':') + '.' + widget.name;
        if (['seed', 'noise_seed'].includes(widget.name) && Number.isInteger(output[path.join(':')]?.inputs[widget.name])) {
          const control = controls.find(item => item.name === 'control_after_generate') || widgets.find(item => item.name === 'control_after_generate');
          seed_modes[address] = control?.value === 'fixed' ? 'fixed' : 'randomize';
          seed_widgets[address] = [{path, index, name: widget.name}];
        }
        if (node.isSubgraphNode?.() && node.subgraph && widget.serialize !== false) {
          const input = node.getSlotFromWidget?.(widget);
          const targets = input ? node.resolveSubgraphInputLinks?.(node.inputs.indexOf(input)) || [] : [];
          for (const target of targets) {
            const source = target?.inputNode?.getWidgetFromSlot(target.input);
            if (source && ['seed', 'noise_seed'].includes(source.name))
              promoted.push({address: [...path, String(target.inputNode.id)].join(':') + '.' + source.name, owner: {path, index, name: widget.name}});
          }
        }
        if (widget.serialize !== false) index++;
      }
    }
  }
  for (const {address, owner} of promoted) if (seed_widgets[address]) seed_widgets[address].push(owner);
  return {seed_modes, seed_widgets};
}
async function publish(pair, mode = 'saved') {
  const controls = seedControls(pair.output, mode);
  if (mode !== 'saved') pair = await app.graphToPrompt();
  const result = await request('state', {client_id: api.clientId, revision, ...pair, seed_mode: 'saved', ...controls});
  revision = result.revision;
  baseline = fingerprint(pair.workflow);
  forcePublish = false;
}
/* Apply the resolution chosen in the settings. `ask` leaves the conflict for
 * the user; `load` takes the LLM graph, `keep` republishes this screen, and
 * `restore` brings back the graph from before the first sync. */
async function applyResolve(value) {
  const key = resolveKey(value);
  resolveMode = key;
  if (key === 'ask' || busy) return;
  try {
    if (key === 'keep' || key === 'restore') {
      if (key === 'restore') {
        const saved = localStorage.getItem('ComfyCollaboration.beforeSync');
        if (!saved) throw new Error(M.errNoRestore);
        loading = true; try {await load(JSON.parse(saved));} finally {loading = false;}
      }
      revision = (await request('state')).revision;
      forcePublish = true;
    } else {
      localStorage.setItem('ComfyCollaboration.beforeSync', JSON.stringify(app.graph.serialize()));
      revision = -1;
    }
    blocked = false; await sync();
  } catch (error) {warn(error);}
}
async function sync() {
  if (!enabled || !booted || busy || !app.graph) return;
  busy = true;
  try {
    if (blocked) {
      const remote = await request('state');
      const local = await app.graphToPrompt();
      // A native Save can resolve the conflict through the named-file watcher.
      // Resume automatically only when no local edits would be discarded.
      if (!remote.workflow || fingerprint(remote.workflow) !== fingerprint(local.workflow)) {
        if (resolveMode === 'load') {
          localStorage.setItem('ComfyCollaboration.beforeSync', JSON.stringify(app.graph.serialize()));
          revision = -1; blocked = false;
        } else {
          return;
        }
      } else {
        revision = remote.revision;
        baseline = fingerprint(local.workflow);
        blocked = false;
      }
    }
    const writable = await lease();
    const pair = revision >= 0 ? await app.graphToPrompt() : null;
    if (pair && (forcePublish || fingerprint(pair.workflow) !== baseline)) {
      if (!writable) {
        warn(new Error(M.errBusy));
        return;
      }
      await lease(true);
      await publish(pair);
    } else {
      const remote = await request('state?revision=' + revision);
      if (remote.revision !== revision) {
        if (revision < 0) localStorage.setItem('ComfyCollaboration.beforeSync', JSON.stringify(app.graph.serialize()));
        loading = true;
        try {
          if (remote.workflow || remote.output) await load(remote.workflow || remote.output);
          revision = remote.revision;
          const loaded = await app.graphToPrompt();
          if (writable) await publish(loaded, remote.seed_mode);
          else {
            seedControls(loaded.output, remote.seed_mode);
            baseline = fingerprint((await app.graphToPrompt()).workflow);
          }
          if (autoOpen && remote.open_request && remote.open_request !== openRequest) {
            app.extensionManager.toast.add({severity: 'info', summary: M.okOpened, detail: M.okOpenedDetail, life: 5000});
          }
          openRequest = remote.open_request;
        } finally {loading = false;}
      }
    }
    await lease();
    lastError = '';
  } catch (error) {
    if (/changed elsewhere|Another ComfyUI/.test(error.message)) blocked = true;
    warn(error);
  } finally {busy = false;}
}
function event(type, detail) {api.dispatchEvent(new CustomEvent(type, {detail}));}
function progress(job) {
  if (!enabled || job.client_id === api.clientId) return;
  const previous = mirrored.get(job.id);
  if (job.status === 'running') {
    if (!previous) event('execution_start', {prompt_id: job.id});
    if (job.current_node) event('executing', job.current_node);
    if (job.max) event('progress', {prompt_id: job.id, node: job.progress_node, value: job.value, max: job.max});
  } else if (job.status === 'completed' && previous !== 'completed') {
    for (const [node, output] of Object.entries(job.outputs || {}))
      event('executed', {prompt_id: job.id, node, display_node: node, output});
    event('executing', null);
    event('execution_success', {prompt_id: job.id});
  } else if (job.status === 'error' && previous !== 'error') {
    event('executing', null);
    warn(new Error(M.errGenFailed));
  }
  mirrored.set(job.id, job.status);
  if (mirrored.size > 20) mirrored.delete(mirrored.keys().next().value);
}
app.registerExtension({
  name: 'Comfy.Collaboration',
  setup() {
    LANG = detectLang();
    M = I18N[LANG];
    app.ui.settings.addSetting({id: 'ComfyCollaboration.Sync', name: M.settingSync, type: 'boolean', defaultValue: true,
      onChange(value) {enabled = value; if (value) {blocked = false; sync();}}
    });
    app.ui.settings.addSetting({id: 'ComfyCollaboration.AutoOpen', name: M.settingAutoOpen, type: 'boolean', defaultValue: true,
      onChange(value) {autoOpen = value; sync();}
    });
    app.ui.settings.addSetting({id: 'ComfyCollaboration.Resolve', name: M.settingResolve, type: 'combo',
      defaultValue: RESOLVE.load[LANG], options: resolveOptions(LANG),
      async onChange(value) {await applyResolve(value);}
    });
    // Adopt the stored value (or the new default: load the LLM changes).
    try {resolveMode = resolveKey(app.ui.settings.getSettingValue('ComfyCollaboration.Resolve'));} catch (e) {resolveMode = 'load';}
    api.addEventListener('workflow_sync_changed', sync);
    api.addEventListener('workflow_sync_progress', message => progress(message.detail));
    api.addEventListener('workflow_sync_error', message => warn(new Error(message.detail.message)));
    setInterval(sync, 750);
  },
  afterConfigureGraph() {if (!booted) {booted = true; queueMicrotask(sync);} }
});
