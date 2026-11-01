"""Backend regressions without a running ComfyUI server."""
import ast
import asyncio
import copy
import hashlib
import json
import logging
from pathlib import Path
import secrets
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import uuid

from aiohttp import web


class SyncTests(unittest.TestCase):
    def setUp(self):
        source = ast.parse((Path(__file__).resolve().parents[1] / '__init__.py').read_text(encoding='utf8'))
        functions = [item for item in source.body if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))]
        for function in functions:
            function.decorator_list = []
        self.scope = dict(globals())
        exec(compile(ast.Module(body=functions, type_ignores=[]), '__init__.py', 'exec'), self.scope)
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.state = {'revision': 0, 'workflow': None, 'output': None, 'seed_mode': 'saved',
                      'seed_modes': {}, 'seed_widgets': {}, 'auto_open': True, 'open_request': None}
        self.events = []
        self.scope.update(ROOT=self.root, FILES={key: self.root / (key + '.json') for key in ('workflow', 'output')},
                          META=self.root / 'sync.json', STATE=self.state, LOCK=threading.RLock(), HASHES={},
                          SAVED_HASHES={}, EDITOR={'client_id': 'browser'}, TRACKED={},
                          SERVER=SimpleNamespace(send_sync=lambda *args: self.events.append(args)),
                          node_info=lambda kind: {'output_node': kind == 'Output'})
        self.prompt = {'1': {'class_type': 'Output', 'inputs': {'seed': 7}}}
        self.workflow = {'id': 'shared', 'nodes': [{'id': 1, 'type': 'Output', 'widgets_values': [7]}]}
        self.state.update(workflow=copy.deepcopy(self.workflow), output=copy.deepcopy(self.prompt))
        for key in ('workflow', 'output'):
            self.scope['atomic_json'](self.scope['FILES'][key], self.state[key])
            self.scope['HASHES'][key] = self.scope['signature'](self.scope['FILES'][key])[0]

    def test_browser_run_preserves_submitted_seed_and_workflow(self):
        self.state.update(seed_mode='randomize', seed_widgets={'1.seed': [{'path': ['1'], 'index': 0}]})
        submitted = copy.deepcopy(self.workflow)
        submitted['nodes'][0]['title'] = 'Unsynced screen edit'
        with patch.object(secrets, 'randbelow', return_value=99):
            result = self.scope['transform_prompt']({'client_id': 'browser', 'prompt': self.prompt,
                                                     'extra_data': {'extra_pnginfo': {'workflow': submitted}}})
        self.assertEqual(result['prompt']['1']['inputs']['seed'], 7)
        self.assertEqual(result['extra_data']['extra_pnginfo']['workflow'], submitted)

    def test_external_run_randomizes_and_updates_workflow(self):
        self.state.update(seed_mode='randomize', seed_widgets={'1.seed': [{'path': ['1'], 'index': 0}]})
        with patch.object(secrets, 'randbelow', return_value=99):
            result = self.scope['transform_prompt']({'prompt': self.prompt})
        self.assertEqual(result['prompt']['1']['inputs']['seed'], 99)
        self.assertEqual(result['extra_data']['extra_pnginfo']['workflow']['nodes'][0]['widgets_values'], [99])
        self.assertEqual(self.prompt['1']['inputs']['seed'], 7)

    def test_changed_seed_connections_do_not_reuse_the_old_ui_graph(self):
        current = {'1': {'class_type': 'Output', 'inputs': {'seed': ['2', 0]}},
                   '2': {'class_type': 'Pass', 'inputs': {'value': 7}},
                   '3': {'class_type': 'Pass', 'inputs': {'value': 8}},
                   '4': {'class_type': 'Output', 'inputs': {'seed': ['3', 0]}}}
        self.state['output'] = current
        changed = copy.deepcopy(current)
        changed['1']['inputs']['seed'] = ['3', 0]
        changed['4']['inputs']['seed'] = ['2', 0]
        self.scope['transform_prompt']({'prompt': changed})
        self.assertIsNone(self.state['workflow'])
        self.assertEqual(self.state['output'], changed)

    def test_partial_json_write_does_not_reject_generation(self):
        self.scope['FILES']['workflow'].write_text('{', encoding='utf8')
        result = self.scope['on_prompt']({'client_id': 'browser', 'prompt': self.prompt})
        self.assertEqual(result['prompt'], self.prompt)
        self.assertEqual(self.state['workflow'], self.workflow)

    def test_invalid_shared_shapes_are_retried_without_poisoning_state(self):
        for key, invalid in [('workflow', []), ('workflow', {'nodes': None}),
                             ('output', []), ('output', {'1': {'inputs': None}})]:
            with self.subTest(key=key, invalid=invalid):
                before = copy.deepcopy(self.state)
                self.scope['atomic_json'](self.scope['FILES'][key], invalid)
                self.scope['refresh_files']()
                self.assertEqual(self.state, before)
                self.scope['atomic_json'](self.scope['FILES'][key], before[key])
        updated = copy.deepcopy(self.workflow)
        updated['nodes'][0]['title'] = 'Completed write'
        self.scope['atomic_json'](self.scope['FILES']['workflow'], updated)
        self.scope['refresh_files']()
        self.assertEqual(self.state['workflow'], updated)
        self.assertEqual(json.loads(self.scope['FILES']['output'].read_text()), {})

    def test_prune_accepts_a_deep_graph_within_the_node_limit(self):
        prompt = {str(i): {'class_type': 'Output' if i == 0 else 'Pass',
                          'inputs': {'value': [str(i + 1), 0]} if i < 1499 else {'value': 1}}
                  for i in range(1500)}
        self.assertEqual(self.scope['prune'](prompt), prompt)

    def test_prune_switches_and_keeps_partial_execution_targets(self):
        prompt = {'out': {'class_type': 'Output', 'inputs': {'value': ['switch', 0]}},
                  'switch': {'class_type': 'ComfySwitchNode', 'inputs': {
                      'switch': True, 'on_true': ['yes', 0], 'on_false': ['no', 0]}},
                  'yes': {'class_type': 'Pass', 'inputs': {'value': 1}},
                  'no': {'class_type': 'Pass', 'inputs': {'value': 2}}}
        result = self.scope['prune'](prompt, ['no'])
        self.assertEqual(set(result), {'out', 'yes', 'no'})
        self.assertEqual(result['out']['inputs']['value'], ['yes', 0])
        self.assertIn('on_false', prompt['switch']['inputs'])

    def test_revision_conflict_does_not_write_files(self):
        class Request:
            async def json(request):
                return {'client_id': 'browser', 'revision': -1, 'workflow': self.workflow, 'output': {}}
        before = {key: path.read_bytes() for key, path in self.scope['FILES'].items()}
        with self.assertRaises(web.HTTPConflict):
            asyncio.run(self.scope['update_state'](Request()))
        self.assertEqual(before, {key: path.read_bytes() for key, path in self.scope['FILES'].items()})

    def test_partial_write_cannot_be_overwritten_by_a_browser_publish(self):
        class Request:
            async def json(request):
                return {'client_id': 'browser', 'revision': 0, 'workflow': self.workflow, 'output': self.prompt}
        self.scope['FILES']['workflow'].write_text('{', encoding='utf8')
        with self.assertRaises(web.HTTPConflict):
            asyncio.run(self.scope['update_state'](Request()))
        self.assertEqual(self.scope['FILES']['workflow'].read_text(), '{')

    def test_external_run_does_not_overwrite_an_incomplete_file(self):
        self.scope['FILES']['workflow'].write_text('{', encoding='utf8')
        self.scope['transform_prompt']({'prompt': self.prompt})
        self.assertEqual(self.scope['FILES']['workflow'].read_text(), '{')

    def restart(self):
        source = (Path(__file__).resolve().parents[1] / '__init__.py').read_text(encoding='utf8')
        self.scope['SERVER'].add_on_prompt_handler = lambda handler: None
        with patch.object(threading, 'Thread'):
            exec(compile(source[source.index('\nif META.exists():'):], '__init__.py', 'exec'), self.scope)

    def test_ui_edit_while_stopped_invalidates_the_old_api_graph(self):
        self.scope['save_meta']()
        updated = copy.deepcopy(self.workflow)
        updated['nodes'][0]['widgets_values'] = [42]
        self.scope['atomic_json'](self.scope['FILES']['workflow'], updated)
        self.restart()
        self.assertEqual(self.state['workflow'], updated)
        self.assertIsNone(self.state['output'])
        self.assertEqual(json.loads(self.scope['FILES']['output'].read_text()), {})
        self.assertEqual(self.state['revision'], 1)

    def test_api_edit_while_stopped_does_not_restore_the_old_ui(self):
        self.scope['save_meta']()
        updated = {'2': {'class_type': 'Output', 'inputs': {'seed': 42}}}
        self.scope['atomic_json'](self.scope['FILES']['output'], updated)
        self.restart()
        self.assertEqual(self.state['output'], updated)
        self.assertIsNone(self.state['workflow'])

    def test_unchanged_files_do_not_create_a_revision_on_restart(self):
        self.scope['save_meta']()
        self.restart()
        self.assertEqual(self.state['revision'], 0)
        self.assertEqual(self.state['output'], self.prompt)

    def test_invalid_lease_does_not_claim_the_editor(self):
        class Request:
            async def json(request):
                return {'client_id': 'new-browser', 'protocol': 1, 'auto_open': 'yes'}
        before = dict(self.scope['EDITOR'])
        with self.assertRaises(web.HTTPBadRequest):
            asyncio.run(self.scope['lease'](Request()))
        self.assertEqual(self.scope['EDITOR'], before)

    def test_invalid_request_json_returns_bad_request(self):
        class Request:
            async def json(request):
                return json.loads('{')
        with self.assertRaises(web.HTTPBadRequest):
            asyncio.run(self.scope['body'](Request()))


if __name__ == '__main__':
    unittest.main()
