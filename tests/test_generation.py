import argparse
import contextlib
import io
import json
import os
import tempfile
from pathlib import Path
import queue
import threading
import time
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from lchat import complete, generate
from lchat_web import DEFAULTS, make_handler


class Handler(BaseHTTPRequestHandler):
    calls = []

    def log_message(self, *args):
        pass

    def do_POST(self):
        payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        self.calls.append(payload)
        if payload.get('stream_options'):
            self.send_response(400)
            self.end_headers()
            return
        self.send_response(200)
        self.end_headers()
        if payload['stream']:
            for obj in [
                {'choices': [{'delta': {'reasoning_content': 'thought'}}]},
                {'choices': [{'delta': {'content': 'answer'}}]},
                {'choices': [], 'usage': {'completion_tokens': 4}},
            ]:
                self.wfile.write(b'data: ' + json.dumps(obj).encode() + b'\n\n')
            self.wfile.write(b'data: [DONE]\n\n')
        else:
            self.wfile.write(json.dumps({'choices': [{'message': {'content': 'answer', 'reasoning': 'thought'}}], 'usage': {'completion_tokens': 4}}).encode())


class GenerationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join()

    def test_opt_in_logs_for_tui_and_plain(self):
        url = f'http://127.0.0.1:{self.server.server_port}/v1/chat/completions'
        with tempfile.TemporaryDirectory() as directory:
            log_dir = Path(directory) / 'log'
            events = queue.Queue()
            generate(argparse.Namespace(api_key='secret-key', timeout=3, log_dir=log_dir), url, events,
                     {'stream': True, 'messages': [{'role': 'user', 'content': 'private prompt'}]}, time.monotonic())
            self.assertEqual(next(data for kind, data in events.queue if kind == 'done')['content'], 'answer')
            raw = next(log_dir.glob('*.sse'))
            self.assertIn(b'reasoning_content', raw.read_bytes())
            self.assertIn(b'data: [DONE]', raw.read_bytes())
            self.assertEqual(next(log_dir.glob('*.answer.txt')).read_text(), 'answer')
            self.assertEqual(next(log_dir.glob('*.thinking.txt')).read_text(), 'thought')
            for path in log_dir.iterdir():
                self.assertNotIn(b'secret-key', path.read_bytes())
                self.assertNotIn(b'private prompt', path.read_bytes())
                if os.name != 'nt':
                    self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(complete(url, {'stream': False}, 'secret-key', 3, log_dir), 'answer')
            self.assertEqual(json.loads(next(log_dir.glob('*.json')).read_text())['choices'][0]['message']['content'], 'answer')
            self.assertEqual(len(list(log_dir.glob('*.answer.txt'))), 2)
            self.assertEqual(len(list(log_dir.glob('*.thinking.txt'))), 2)

    def test_web_logs_upstream_response(self):
        with tempfile.TemporaryDirectory() as directory:
            log_dir = Path(directory) / 'log'
            config = {**DEFAULTS, 'endpoint': f'http://127.0.0.1:{self.server.server_port}/v1', 'model': 'model'}
            state = {'config': config, 'config_path': Path(directory) / 'config.json', 'lock': threading.RLock(), 'quiet': True, 'log_dir': log_dir}
            server = ThreadingHTTPServer(('127.0.0.1', 0), make_handler(state))
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = urllib.request.Request(f'http://127.0.0.1:{server.server_port}/api/chat',
                    data=json.dumps({'messages': [{'role': 'user', 'content': 'private prompt'}]}).encode(),
                    headers={'Content-Type': 'application/json'})
                with urllib.request.urlopen(request, timeout=5) as response:
                    self.assertIn(b'[DONE]', response.read())
                self.assertEqual(next(log_dir.glob('*.answer.txt')).read_text(), 'answer')
                self.assertEqual(next(log_dir.glob('*.thinking.txt')).read_text(), 'thought')
                self.assertIn(b'reasoning_content', next(log_dir.glob('*.sse')).read_bytes())
            finally:
                server.shutdown()
                server.server_close()
                thread.join()

    def test_stream_and_nonstream(self):
        for streaming in (True, False):
            Handler.calls.clear()
            events = queue.Queue()
            url = f'http://127.0.0.1:{self.server.server_port}/v1/chat/completions'
            generate(argparse.Namespace(api_key='', timeout=3), url, events, {'stream': streaming}, time.monotonic())
            result = list(events.queue)
            done = next(data for kind, data in result if kind == 'done')
            self.assertEqual((done['content'], done['thinking']), ('answer', 'thought'))
            self.assertEqual(done['metrics']['tokens'], 4)
            self.assertEqual(len(Handler.calls), 2 if streaming else 1)
            if streaming:
                self.assertNotIn('stream_options', Handler.calls[-1])
