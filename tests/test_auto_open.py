"""Run with python test_auto_open.py; no ComfyUI server or generation needed."""
import ast
import copy
import hashlib
import json
from pathlib import Path
import secrets
import tempfile
import threading
import time
from types import SimpleNamespace
import uuid


source = ast.parse(Path(__file__).with_name('__init__.py').read_text(encoding='utf8'))
functions = ast.Module(body=[item for item in source.body if isinstance(item, ast.FunctionDef)], type_ignores=[])
scope = dict(globals())
exec(compile(functions, '__init__.py', 'exec'), scope)
with tempfile.TemporaryDirectory() as directory:
    root = Path(directory)
    state = {'revision': 0, 'workflow': None, 'output': None, 'seed_mode': 'saved',
             'seed_modes': {}, 'seed_widgets': {}, 'auto_open': True, 'open_request': None}
    events = []
    scope.update(ROOT=root, FILES={key: root / (key + '.json') for key in ('workflow', 'output')},
                 META=root / 'sync.json', LOCK=threading.RLock(), STATE=state, HASHES={},
                 SAVED_HASHES={}, EDITOR={'client_id': 'browser'}, TRACKED={},
                 SERVER=SimpleNamespace(send_sync=lambda *args: events.append(args)),
                 node_info=lambda kind: {'output_node': True})
    prompt = {'1': {'class_type': 'ExampleOutput', 'inputs': {'value': 1}}}
    run = scope['transform_prompt']
    result = run({'prompt': prompt})
    assert state['output'] == prompt and state['workflow'] is None
    assert state['open_request'] == result['prompt_id'] and state['revision'] == 1
    assert json.loads(scope['FILES']['output'].read_text()) == prompt
    assert result['prompt_id'] in scope['TRACKED']
    revision = state['revision']
    state['auto_open'] = False
    other = {'2': {'class_type': 'ExampleOutput', 'inputs': {'value': 2}}}
    run({'prompt': other})
    assert state['revision'] == revision and state['output'] == prompt
    state['auto_open'] = True
    run({'prompt': other, 'client_id': 'browser'})
    assert state['revision'] == revision
    workflow = {'nodes': [{'id': 2, 'type': 'ExampleOutput'}]}
    state['seed_modes'] = {'old.seed': 'randomize'}
    result = run({'prompt': other, 'extra_data': {'extra_pnginfo': {'workflow': workflow}}})
    assert state['workflow'] == workflow and state['seed_modes'] == {}
    assert result['extra_data']['extra_pnginfo']['workflow'] == workflow
    revision = state['revision']
    run({'prompt': other})
    assert state['revision'] == revision + 1 and state['workflow'] == workflow
    request = state['open_request']
    workflow['nodes'][0]['title'] = 'External edit'
    scope['atomic_json'](scope['FILES']['workflow'], workflow)
    scope['refresh_files']()
    assert state['workflow'] == workflow and state['open_request'] != request
    revision = state['revision']
    scope['refresh_files']()
    assert state['revision'] == revision
print('Auto-open checks passed')
