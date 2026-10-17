"""Workflow Sync - ComfyUI collaboration with an LLM via the official Comfy MCP.

The extension exposes the currently shared workflow over HTTP so an LLM agent
(through the official Comfy MCP) and the ComfyUI front-end can edit the same
graph. It uses only the stock ComfyUI server and MCP; no third-party service.

Copyright 2026 Workflow Sync contributors
Licensed under the Apache License, Version 2.0 (see LICENSE).
"""
import copy
import hashlib
import json
import logging
from pathlib import Path
import secrets
import threading
import time
import uuid

from aiohttp import web
import folder_paths
import nodes
from server import PromptServer

__version__ = "1.0.0"

WEB_DIRECTORY = "."
NODE_CLASS_MAPPINGS = {}
NODE_DISPLAY_NAME_MAPPINGS = {}
ROOT = Path(folder_paths.get_user_directory()) / 'default' / 'workflows'
ROOT.mkdir(parents=True, exist_ok=True)
FILES = {'workflow': ROOT / 'Workflow collaboration.ui.json',
         'output': ROOT / 'Workflow collaboration.api.json'}
META = ROOT / 'Workflow collaboration.sync.json'
LOCK = threading.RLock()
EDITOR = {}
TRACKED = {}
STATE = {'revision': 0, 'workflow': None, 'output': None, 'seed_mode': 'randomize',
         'seed_modes': {}, 'seed_widgets': {}, 'auto_open': True, 'open_request': None}
HASHES = {}
SAVED_HASHES = {}
SERVER = PromptServer.instance


def atomic_json(path, data):
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False), encoding='utf8')
    temporary.replace(path)


def signature(path):
    if not path.exists():
        return None, None
    raw = path.read_bytes()
    return hashlib.sha256(raw).hexdigest(), json.loads(raw)


def save_meta():
    atomic_json(META, {key: value for key, value in STATE.items() if key not in FILES} |
                {'ui_current': STATE['workflow'] is not None})


def announce():
    SERVER.send_sync('workflow_sync_changed', {'revision': STATE['revision']})


def refresh_files():
    with LOCK:
        observed = {key: signature(path) for key, path in FILES.items()}
        changed = {}
        for key, (stamp, data) in observed.items():
            if stamp != HASHES.get(key):
                changed[key] = data
        # Native Save writes the named workflow, not the collaboration file.
        # Only follow changed files belonging to the currently shared graph.
        graph_id = (STATE.get('workflow') or {}).get('id')
        saved = []
        for path in ROOT.rglob('*.json'):
            if path in (*FILES.values(), META):
                continue
            try:
                stamp = path.stat().st_mtime_ns
                previous = SAVED_HASHES.get(path)
                if stamp == previous:
                    continue
                _, data = signature(path)
                SAVED_HASHES[path] = stamp
                if graph_id and isinstance(data, dict) and data.get('id') == graph_id and isinstance(data.get('nodes'), list):
                    saved.append((stamp, data))
            except (ValueError, OSError):
                continue  # Retry incomplete writes without acknowledging their timestamp.
        if saved and not changed:
            _, workflow = max(saved, key=lambda item: item[0])
            if workflow != STATE.get('workflow'):
                atomic_json(FILES['workflow'], workflow)
                observed['workflow'] = signature(FILES['workflow'])
                changed['workflow'] = workflow
        if not changed:
            return
        if STATE['auto_open'] and any(key in changed for key in FILES) and not saved:
            STATE['open_request'] = str(uuid.uuid4())
        HASHES.update({key: stamp for key, (stamp, _) in observed.items()})
        if 'workflow' in changed:
            STATE.update(workflow=changed['workflow'], output=None)
            # The official runner must not consume an API graph from before the UI edit.
            atomic_json(FILES['output'], {})
            HASHES['output'] = signature(FILES['output'])[0]
        else:
            STATE.update(output=changed['output'], workflow=None)
        STATE['revision'] += 1
        save_meta()
        announce()


def node_info(kind):
    node = nodes.NODE_CLASS_MAPPINGS.get(kind)
    if node is None:
        return {}
    if hasattr(node, 'GET_NODE_INFO_V1'):
        return node.GET_NODE_INFO_V1()
    return {'input': node.INPUT_TYPES(), 'output_node': bool(getattr(node, 'OUTPUT_NODE', False))}


def prune(prompt, additional_roots=()):
    """Resolve constant switches before Comfy validates their unused lazy branches."""
    prompt = copy.deepcopy(prompt)
    for node in prompt.values():
        fields = node['inputs']
        if node['class_type'] == 'ComfySwitchNode' and type(fields.get('switch')) is bool:
            fields.pop('on_false' if fields['switch'] else 'on_true', None)
    for node in prompt.values():
        for name, value in list(node['inputs'].items()):
            seen = set()
            while isinstance(value, list) and len(value) == 2 and str(value[0]) in prompt:
                source = prompt[str(value[0])]
                fields = source['inputs']
                if source['class_type'] != 'ComfySwitchNode' or type(fields.get('switch')) is not bool:
                    break
                branch = 'on_true' if fields['switch'] else 'on_false'
                if branch not in fields:
                    break
                if str(value[0]) in seen:
                    raise ValueError('Cyclic constant switches')
                seen.add(str(value[0]))
                value = fields[branch]
            node['inputs'][name] = value
    roots = [key for key, node in prompt.items() if node_info(node['class_type']).get('output_node')]
    roots.extend(str(key) for key in additional_roots if str(key) in prompt and str(key) not in roots)
    if not roots:
        return prompt  # Comfy's validator reports missing output nodes.
    needed = set()
    def visit(key):
        if key in needed:
            return
        needed.add(key)
        for value in prompt[key]['inputs'].values():
            if isinstance(value, list) and len(value) == 2 and str(value[0]) in prompt:
                visit(str(value[0]))
    for key in roots:
        visit(key)
    return {key: value for key, value in prompt.items() if key in needed}


def comparable(prompt):
    return {key: {'class_type': node['class_type'], 'inputs': {
        name: value for name, value in node['inputs'].items() if name not in ('seed', 'noise_seed')}}
        for key, node in prompt.items()}


def randomize_seeds(prompt):
    targets = {}
    for key, node in prompt.items():
        specs = {name: spec for fields in node_info(node['class_type']).get('input', {}).values()
                 for name, spec in fields.items()}
        for name, value in node['inputs'].items():
            if name not in ('seed', 'noise_seed'):
                continue
            slot = f'{key}.{name}'
            target = (key, name)
            if isinstance(value, list) and len(value) == 2:
                upstream = prompt.get(str(value[0]), {}).get('inputs', {})
                if len(upstream) != 1 or type(upstream.get('value')) is not int:
                    continue  # Computed seeds remain under the graph's control.
                target = (str(value[0]), 'value')
                value = upstream['value']
            if type(value) is not int:
                continue
            mode = STATE['seed_modes'].get(slot, STATE['seed_mode'])
            if mode == 'saved':
                mode = STATE['seed_modes'].get(slot, 'fixed')
            spec = specs.get(name, ['INT', {}])
            options = spec[1] if len(spec) > 1 and isinstance(spec[1], dict) else {}
            minimum, maximum = int(options.get('min', 0)), int(options.get('max', 2**64 - 1))
            group = targets.setdefault(target, {'min': minimum, 'max': maximum, 'modes': set(), 'slots': []})
            group['min'], group['max'] = max(group['min'], minimum), min(group['max'], maximum)
            group['modes'].add(mode)
            group['slots'].append(slot)
    actual = {}
    for (key, name), group in targets.items():
        if len(group['modes']) != 1:
            raise ValueError('Shared seed has conflicting controls')
        minimum, maximum = group['min'], group['max']
        mode = next(iter(group['modes']))
        if mode == 'randomize':
            maximum = min(maximum, 2**50)
            if maximum < minimum:
                raise ValueError('Empty random seed range')
            prompt[key]['inputs'][name] = minimum + secrets.randbelow(maximum - minimum + 1)
        value = prompt[key]['inputs'][name]
        if not minimum <= value <= maximum:
            raise ValueError('Seed is outside node limits')
        actual[f'{key}.{name}'] = value
        actual.update({slot: value for slot in group['slots']})
    return actual


def actual_workflow(actual):
    workflow = copy.deepcopy(STATE['workflow'])
    if not workflow:
        return None
    definitions = {item['id']: item for item in workflow.get('definitions', {}).get('subgraphs', [])}
    for slot, value in actual.items():
        owners = STATE['seed_widgets'].get(slot)
        if not owners:
            continue
        for owner in owners if isinstance(owners, list) else [owners]:
            container = workflow
            for index, key in enumerate(owner['path']):
                node = next((node for node in container.get('nodes', []) if str(node['id']) == str(key)), None)
                if node is None:
                    break
                if index == len(owner['path']) - 1:
                    values = node.get('widgets_values', [])
                    if type(owner['index']) is int and 0 <= owner['index'] < len(values):
                        values[owner['index']] = value
                    named = node.get('widgets_values_named', {})
                    if isinstance(named, dict) and owner.get('name') in named:
                        named[owner['name']] = value
                else:
                    container = definitions.get(node['type'], {})
    return workflow


def transform_prompt(data):
    with LOCK:
        refresh_files()
        current = STATE.get('output')
        submitted = data.get('prompt')
        if not isinstance(submitted, dict):
            return data
        data = copy.deepcopy(data)
        targets = data.get('partial_execution_targets')
        prompt = prune(submitted, targets if isinstance(targets, list) else ())
        data['prompt'] = prompt
        matching = bool(current) and comparable(prompt) == comparable(prune(current))
        external = data.get('client_id') != EDITOR.get('client_id') or not data.get('client_id')
        if not matching and not (external and STATE['auto_open']):
            return data
        actual = randomize_seeds(prompt) if matching else {}
        pid = data.setdefault('prompt_id', str(uuid.uuid4()))
        extra = data.setdefault('extra_data', {}).setdefault('extra_pnginfo', {})
        workflow = actual_workflow(actual) if matching else extra.get('workflow')
        if not isinstance(workflow, dict) or not isinstance(workflow.get('nodes'), list):
            workflow = None
        if workflow is not None:
            extra['workflow'] = workflow
        if external and STATE['auto_open']:
            for key, value in [('workflow', workflow), ('output', prompt)]:
                atomic_json(FILES[key], value)
                HASHES[key] = signature(FILES[key])[0]
            STATE.update(workflow=workflow, output=prompt, revision=STATE['revision'] + 1, open_request=pid)
            if not matching:
                STATE.update(seed_mode='saved', seed_modes={}, seed_widgets={})
            save_meta()
        TRACKED[pid] = {'id': pid, 'status': 'queued', 'created': time.time(), 'seeds': actual,
                        'client_id': data.get('client_id'), 'images': []}
        while len(TRACKED) > 20:
            TRACKED.pop(next(iter(TRACKED)))
        announce()
        return data


def on_prompt(data):
    try:
        return transform_prompt(data)
    except (ValueError, KeyError, TypeError) as error:
        # Comfy's handler dispatcher catches exceptions and otherwise submits the original.
        # Reject the prompt rather than silently ignoring a conflicting seed policy.
        SERVER.send_sync('workflow_sync_error', {'message': str(error)})
        return {**data, 'prompt': {}}


def public_state(full=True):
    with LOCK:
        refresh_files()
        state = {key: copy.deepcopy(value) for key, value in STATE.items() if full or key not in FILES}
        state['editor'] = {**EDITOR, 'connected': time.monotonic() - EDITOR.get('seen', -100) < 8}
        state['jobs'] = copy.deepcopy(list(TRACKED.values()))[-3:]
        if full:
            state['paths'] = {key: str(value) for key, value in FILES.items()}
        return state


async def body(request):
    data = await request.json()
    if not isinstance(data, dict):
        raise web.HTTPBadRequest(text='JSON object required')
    return data


@SERVER.routes.get('/workflow-sync/state')
async def get_state(request):
    with LOCK:
        refresh_files()
        return web.json_response(public_state(request.query.get('revision') != str(STATE['revision'])))


@SERVER.routes.post('/workflow-sync/lease')
async def lease(request):
    data = await body(request)
    if data.get('protocol') != 1:
        raise web.HTTPBadRequest(text='Reload ComfyUI to update the collaboration extension')
    if not isinstance(data.get('client_id'), str) or not data['client_id']:
        raise web.HTTPBadRequest(text='Editor client id required')
    with LOCK:
        ready = data['client_id'] == EDITOR.get('client_id') or time.monotonic() - EDITOR.get('seen', -100) >= 8
        if ready:
            EDITOR.update(client_id=data['client_id'], seen=time.monotonic(), editing=bool(data.get('editing')),
                          revision=data.get('revision'))
            if 'auto_open' in data:
                if type(data['auto_open']) is not bool:
                    raise web.HTTPBadRequest(text='Auto open must be a boolean')
                if STATE['auto_open'] != data['auto_open']:
                    STATE['auto_open'] = data['auto_open']
                    save_meta()
        return web.json_response({'ready': ready})


@SERVER.routes.post('/workflow-sync/state')
async def update_state(request):
    data = await body(request)
    with LOCK:
        refresh_files()
        if data.get('client_id') != EDITOR.get('client_id') or not data.get('client_id'):
            raise web.HTTPConflict(text='Another ComfyUI editor is active; keep your edits')
        if type(data.get('revision')) is not int or data['revision'] != STATE['revision']:
            raise web.HTTPConflict(text='Workflow changed elsewhere; keep your edits')
        workflow, output = data.get('workflow'), data.get('output')
        if not isinstance(workflow, dict) or not isinstance(workflow.get('nodes'), list):
            raise web.HTTPBadRequest(text='Native UI workflow required')
        if not isinstance(output, dict) or len(output) > 2000 or any(
                not isinstance(node, dict) or not isinstance(node.get('inputs'), dict) or
                not isinstance(node.get('class_type'), str) for node in output.values()):
            raise web.HTTPBadRequest(text='API workflow must contain at most 2000 nodes')
        mode = data.get('seed_mode', 'saved')
        modes, widgets = data.get('seed_modes', {}), data.get('seed_widgets', {})
        if mode not in ('saved', 'fixed', 'randomize') or not isinstance(modes, dict) or any(
                value not in ('fixed', 'randomize') for value in modes.values()):
            raise web.HTTPBadRequest(text='Invalid seed controls')
        if not isinstance(widgets, dict) or any(not owners or any(not isinstance(owner, dict) or
                not isinstance(owner.get('path'), list) or not owner['path'] or type(owner.get('index')) is not int or
                ('name' in owner and not isinstance(owner['name'], str))
                for owner in (owners if isinstance(owners, list) else [owners])) for owners in widgets.values()):
            raise web.HTTPBadRequest(text='Invalid seed widget owners')
        try:
            output = prune(output)
        except (ValueError, KeyError, TypeError) as error:
            raise web.HTTPBadRequest(text=str(error)) from error
        for key, value in [('workflow', workflow), ('output', output)]:
            atomic_json(FILES[key], value)
            HASHES[key] = signature(FILES[key])[0]
        STATE.update(workflow=workflow, output=output, seed_mode=mode, seed_modes=modes,
                     seed_widgets=widgets, revision=STATE['revision'] + 1)
        save_meta()
        announce()
        return web.json_response({'revision': STATE['revision']})


def update_job_progress(job, pid):
    from comfy_execution.progress import get_progress_state
    registry = get_progress_state()
    if registry.prompt_id != pid:
        return
    entries = list(registry.nodes.items())[-50:]
    job['node_states'] = {key: state['state'].value for key, state in entries}
    measured = [(key, state) for key, state in entries if state['max'] > 1]
    if measured:
        key, state = measured[-1]
        job.update(progress_node=key, value=state['value'], max=state['max'])


def monitor():
    while True:
        try:
            with LOCK:
                refresh_files()
                running, pending = SERVER.prompt_queue.get_current_queue()
                active, queued = {item[1] for item in running}, {item[1] for item in pending}
                for pid, job in TRACKED.items():
                    if job['status'] in ('completed', 'error', 'cancelled'):
                        continue
                    history = SERVER.prompt_queue.get_history(prompt_id=pid).get(pid)
                    before = copy.deepcopy(job)
                    update_job_progress(job, pid)
                    if history:
                        outputs = {key: {field: value for field, value in result.items() if field in ('images', 'animated')}
                                   for key, result in history.get('outputs', {}).items() if result.get('images')}
                        job.update(status='completed' if history.get('status', {}).get('status_str') == 'success' else 'error',
                                   outputs=outputs, images=[image for result in outputs.values() for image in result['images']])
                        if job['status'] == 'error':
                            job['error'] = history.get('status', {}).get('messages', [])
                    elif pid in active:
                        job.update(status='running', current_node=SERVER.last_node_id)
                    elif pid not in queued and time.time() - job['created'] > 5:
                        job['status'] = 'cancelled'
                    if before != job:
                        SERVER.send_sync('workflow_sync_progress', copy.deepcopy(job))
        except Exception:
            logging.debug('Native collaboration monitor is waiting for valid files/server', exc_info=True)
        time.sleep(.75)


if META.exists():
    try:
        metadata = json.loads(META.read_text(encoding='utf8'))
        STATE.update({key: metadata[key] for key in STATE if key in metadata})
    except (ValueError, OSError):
        logging.warning('Cannot read collaboration metadata; recovering from workflow files')
for key, path in FILES.items():
    try:
        HASHES[key], STATE[key] = signature(path)
    except (ValueError, OSError):
        logging.warning('Cannot read shared %s workflow; waiting for a valid JSON file', key)
SAVED_HASHES.update({path: path.stat().st_mtime_ns for path in ROOT.rglob('*.json')
                     if path not in (*FILES.values(), META)})
if META.exists() and not locals().get('metadata', {}).get('ui_current', True):
    STATE['workflow'] = None
SERVER.add_on_prompt_handler(on_prompt)
threading.Thread(target=monitor, daemon=True, name='ComfyNativeSync').start()
