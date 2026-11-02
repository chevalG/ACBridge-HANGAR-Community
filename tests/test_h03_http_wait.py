"""HTTP integration tests for HANGAR job wait and revision notification.

Verifies the HTTP loopback surface for /api/jobs/<job_id> with:
- Query parameters ?after_revision=<rev>&wait_ms=<ms>
- Immediate return on terminal states (completed/failed)
- Notification wakes waiting responses promptly without lost wakeups
- Query parameter validation and boundary enforcement (HTTP 400)
- Legacy GET and HEAD compatibility without wait parameters
- Host and Origin protection
- Server shutdown unblocking waiting HTTP requests
"""
import http.client
import json
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from hangar.app import App
from hangar.server import create_server


class Response:
    def __init__(self, status, reason, headers, body):
        self.status = status
        self.reason = reason
        self.headers = headers
        self.body = body

    def header(self, name):
        return self.headers.get(name.lower())

    def json(self):
        return json.loads(self.body.decode('utf-8'))


class HttpJobWaitIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.TemporaryDirectory(prefix='hangar-http-wait-')
        cls.data_dir = Path(cls.temp_dir.name) / 'data'
        cls.app = App(root=ROOT, data_dir=cls.data_dir, web_dir=ROOT / 'web')
        cls.server = create_server(cls.app, host='127.0.0.1', port=0)
        cls.host, cls.port = cls.server.server_address[:2]
        cls.server_thread = threading.Thread(
            target=cls.server.serve_forever,
            kwargs={'poll_interval': 0.05},
            name='hangar-test-http-wait',
            daemon=True
        )
        cls.server_thread.start()

    @classmethod
    def tearDownClass(cls):
        try:
            cls.server.shutdown()
        except Exception:
            pass
        try:
            cls.server.server_close()
        finally:
            cls.app.close()
            cls.server_thread.join(timeout=5)
            cls.temp_dir.cleanup()

    def request(self, method, path, headers=None, body=None, timeout=10):
        conn = http.client.HTTPConnection(self.host, self.port, timeout=timeout)
        req_headers = {
            'Connection': 'close',
            'Host': f'{self.host}:{self.port}',
        }
        if headers:
            req_headers.update(headers)
        try:
            conn.request(method, path, body=body, headers=req_headers)
            res = conn.getresponse()
            data = res.read()
            hdr_map = {k.lower(): v for k, v in res.getheaders()}
            return Response(res.status, res.reason, hdr_map, data)
        finally:
            conn.close()

    def create_mock_job(self, kind='build', payload=None):
        return self.app.jobs.create(kind, payload or {'mock': True})

    # -------------------------------------------------------------------------
    # 1. Legacy GET and HEAD compatibility
    # -------------------------------------------------------------------------
    def test_legacy_get_without_wait_params(self):
        job = self.create_mock_job()
        res = self.request('GET', f'/api/jobs/{job["id"]}')
        self.assertEqual(res.status, 200)
        self.assertIn('application/json', res.header('content-type'))
        self.assertEqual(res.header('cache-control'), 'no-store')
        data = res.json()
        self.assertEqual(data['id'], job['id'])
        self.assertEqual(data['status'], 'queued')
        self.assertEqual(data['revision'], 0)

    def test_legacy_head_without_wait_params(self):
        job = self.create_mock_job()
        res = self.request('HEAD', f'/api/jobs/{job["id"]}')
        self.assertEqual(res.status, 200)
        self.assertEqual(len(res.body), 0)
        self.assertIn('application/json', res.header('content-type'))
        self.assertIsNotNone(res.header('content-length'))
        self.assertGreater(int(res.header('content-length')), 0)

    def test_head_with_wait_query_does_not_block(self):
        job = self.create_mock_job()
        t0 = time.monotonic()
        res = self.request('HEAD', f'/api/jobs/{job["id"]}?after_revision=0&wait_ms=1000')
        elapsed = time.monotonic() - t0
        self.assertEqual(res.status, 200)
        self.assertEqual(len(res.body), 0)
        self.assertLess(elapsed, 0.25, 'HEAD should return immediately without waiting')
        self.assertGreater(int(res.header('content-length')), 0)

    def test_job_methods_not_allowed(self):
        job = self.create_mock_job()
        for method in ('POST', 'PUT', 'DELETE'):
            res = self.request(method, f'/api/jobs/{job["id"]}')
            self.assertEqual(res.status, 405)
            err = res.json().get('error', {})
            self.assertEqual(err.get('code'), 'method_not_allowed')

    def test_job_not_found(self):
        fake_id = '0123456789abcdef0123456789abcdef'
        res = self.request('GET', f'/api/jobs/{fake_id}')
        self.assertEqual(res.status, 404)
        self.assertEqual(res.json()['error']['code'], 'not_found')

        res_wait = self.request('GET', f'/api/jobs/{fake_id}?after_revision=0&wait_ms=50')
        self.assertEqual(res_wait.status, 404)
        self.assertEqual(res_wait.json()['error']['code'], 'not_found')

    # -------------------------------------------------------------------------
    # 2. Immediate return on terminal states or advanced revision
    # -------------------------------------------------------------------------
    def test_immediate_return_when_already_completed(self):
        job = self.create_mock_job()
        updated = self.app.jobs.update(job['id'], status='completed', result={'valid': True})
        rev = updated['revision']

        t0 = time.monotonic()
        res = self.request('GET', f'/api/jobs/{job["id"]}?after_revision={rev}&wait_ms=2000')
        elapsed = time.monotonic() - t0

        self.assertEqual(res.status, 200)
        data = res.json()
        self.assertEqual(data['status'], 'completed')
        self.assertEqual(data['revision'], rev)
        self.assertLess(elapsed, 0.2, f'Expected immediate return for completed job, took {elapsed}s')

    def test_immediate_return_when_already_failed(self):
        job = self.create_mock_job()
        updated = self.app.jobs.update(job['id'], status='failed', error={'code': 'err', 'message': 'failed'})
        rev = updated['revision']

        t0 = time.monotonic()
        res = self.request('GET', f'/api/jobs/{job["id"]}?after_revision={rev}&wait_ms=2000')
        elapsed = time.monotonic() - t0

        self.assertEqual(res.status, 200)
        data = res.json()
        self.assertEqual(data['status'], 'failed')
        self.assertEqual(data['revision'], rev)
        self.assertLess(elapsed, 0.2, f'Expected immediate return for failed job, took {elapsed}s')

    def test_immediate_return_when_revision_already_advanced(self):
        job = self.create_mock_job()
        self.app.jobs.update(job['id'], status='running', progress=10)

        t0 = time.monotonic()
        res = self.request('GET', f'/api/jobs/{job["id"]}?after_revision=0&wait_ms=2000')
        elapsed = time.monotonic() - t0

        self.assertEqual(res.status, 200)
        data = res.json()
        self.assertEqual(data['status'], 'running')
        self.assertGreater(data['revision'], 0)
        self.assertLess(elapsed, 0.2, f'Expected immediate return when revision is advanced, took {elapsed}s')

    # -------------------------------------------------------------------------
    # 3. Notification wakes response (no lost wakeup)
    # -------------------------------------------------------------------------
    def test_notification_wakes_waiting_get_on_intermediate_progress(self):
        job = self.create_mock_job()
        result_holder = {}

        def do_wait():
            t0 = time.monotonic()
            res = self.request('GET', f'/api/jobs/{job["id"]}?after_revision=0&wait_ms=2000')
            result_holder['elapsed'] = time.monotonic() - t0
            result_holder['response'] = res

        thread = threading.Thread(target=do_wait)
        thread.start()

        # Allow the client request to reach server and wait on condition
        time.sleep(0.06)
        self.app.jobs.update(job['id'], status='running', stage='source-skeleton',
                             progress=35, message='Parsing skeleton')
        thread.join(timeout=3)

        self.assertFalse(thread.is_alive(), 'Wait thread did not exit in time')
        self.assertIn('response', result_holder)
        res = result_holder['response']
        self.assertEqual(res.status, 200)
        data = res.json()
        self.assertEqual(data['status'], 'running')
        self.assertEqual(data['stage'], 'source-skeleton')
        self.assertEqual(data['revision'], 1)
        self.assertAlmostEqual(data['progress'], 0.35, places=2)
        self.assertEqual(data['progress_percent'], 35)
        self.assertLess(result_holder['elapsed'], 0.6,
                        f'Waiter should wake promptly on notification, took {result_holder["elapsed"]}s')

    def test_notification_wakes_waiting_get_on_terminal_completion(self):
        job = self.create_mock_job()
        self.app.jobs.update(job['id'], status='running', progress=80)
        result_holder = {}

        def do_wait():
            t0 = time.monotonic()
            res = self.request('GET', f'/api/jobs/{job["id"]}?after_revision=1&wait_ms=2000')
            result_holder['elapsed'] = time.monotonic() - t0
            result_holder['response'] = res

        thread = threading.Thread(target=do_wait)
        thread.start()

        time.sleep(0.06)
        self.app.jobs.update(job['id'], status='completed', stage='complete',
                             progress=100, message='Export done', result={'valid': True})
        thread.join(timeout=3)

        self.assertFalse(thread.is_alive(), 'Wait thread did not exit in time')
        self.assertIn('response', result_holder)
        res = result_holder['response']
        self.assertEqual(res.status, 200)
        data = res.json()
        self.assertEqual(data['status'], 'completed')
        self.assertEqual(data['revision'], 2)
        self.assertEqual(data['result'], {'valid': True})
        self.assertLess(result_holder['elapsed'], 0.6,
                        f'Waiter should wake on completion, took {result_holder["elapsed"]}s')

    def test_notification_wakes_waiting_get_on_terminal_failure(self):
        job = self.create_mock_job()
        self.app.jobs.update(job['id'], status='running', progress=20)
        result_holder = {}

        def do_wait():
            t0 = time.monotonic()
            res = self.request('GET', f'/api/jobs/{job["id"]}?after_revision=1&wait_ms=2000')
            result_holder['elapsed'] = time.monotonic() - t0
            result_holder['response'] = res

        thread = threading.Thread(target=do_wait)
        thread.start()

        time.sleep(0.06)
        self.app.jobs.update(job['id'], status='failed', stage='failed',
                             error={'code': 'worker_crashed', 'message': 'Simulated failure'})
        thread.join(timeout=3)

        self.assertFalse(thread.is_alive(), 'Wait thread did not exit in time')
        self.assertIn('response', result_holder)
        res = result_holder['response']
        self.assertEqual(res.status, 200)
        data = res.json()
        self.assertEqual(data['status'], 'failed')
        self.assertEqual(data['revision'], 2)
        self.assertEqual(data['error']['code'], 'worker_crashed')
        self.assertLess(result_holder['elapsed'], 0.6,
                        f'Waiter should wake on failure, took {result_holder["elapsed"]}s')

    def test_concurrent_waiters_all_awoken_without_lost_wakeup(self):
        job = self.create_mock_job()
        results = [None] * 4

        def worker(idx):
            t0 = time.monotonic()
            res = self.request('GET', f'/api/jobs/{job["id"]}?after_revision=0&wait_ms=2000')
            results[idx] = (time.monotonic() - t0, res)

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
        for t in threads:
            t.start()

        time.sleep(0.08)
        self.app.jobs.update(job['id'], status='running', progress=50)

        for t in threads:
            t.join(timeout=3)
            self.assertFalse(t.is_alive(), 'Concurrent wait thread did not finish')

        for idx, (elapsed, res) in enumerate(results):
            self.assertEqual(res.status, 200)
            data = res.json()
            self.assertEqual(data['status'], 'running')
            self.assertEqual(data['revision'], 1)
            self.assertLess(elapsed, 0.7, f'Worker {idx} took too long ({elapsed}s) to wake')

    # -------------------------------------------------------------------------
    # 4. Timeout behavior when job state does not change
    # -------------------------------------------------------------------------
    def test_wait_timeout_returns_snapshot_without_mutation(self):
        job = self.create_mock_job()
        t0 = time.monotonic()
        res = self.request('GET', f'/api/jobs/{job["id"]}?after_revision=0&wait_ms=50')
        elapsed = time.monotonic() - t0

        self.assertEqual(res.status, 200)
        data = res.json()
        self.assertEqual(data['id'], job['id'])
        self.assertEqual(data['revision'], 0)
        self.assertEqual(data['status'], 'queued')
        self.assertGreaterEqual(elapsed, 0.04)
        self.assertLess(elapsed, 0.35)

    # -------------------------------------------------------------------------
    # 5. Invalid bounds and parameter validation (HTTP 400)
    # -------------------------------------------------------------------------
    def test_invalid_wait_ms_bounds(self):
        job = self.create_mock_job()
        for wait_val in (-1, -100, 2001, 5000):
            res = self.request('GET', f'/api/jobs/{job["id"]}?after_revision=0&wait_ms={wait_val}')
            self.assertEqual(res.status, 400, f'wait_ms={wait_val} should return 400')
            err = res.json().get('error', {})
            self.assertEqual(err.get('code'), 'invalid_job_wait')

    def test_invalid_after_revision_bounds(self):
        job = self.create_mock_job()
        for rev_val in (-1, -10, 9007199254740992):
            res = self.request('GET', f'/api/jobs/{job["id"]}?after_revision={rev_val}&wait_ms=50')
            self.assertEqual(res.status, 400, f'after_revision={rev_val} should return 400')
            err = res.json().get('error', {})
            self.assertEqual(err.get('code'), 'invalid_job_wait')

    def test_invalid_param_types(self):
        job = self.create_mock_job()
        invalid_queries = [
            'wait_ms=abc',
            'wait_ms=1.5',
            'after_revision=xyz',
            'after_revision=0.5',
            'after_revision=null',
            'wait_ms=undefined',
            'after_revision=true',
            'after_revision=0x10',
            'wait_ms=--1',
        ]
        for query in invalid_queries:
            res = self.request('GET', f'/api/jobs/{job["id"]}?{query}')
            self.assertEqual(res.status, 400, f'query {query} should return 400')
            err = res.json().get('error', {})
            self.assertEqual(err.get('code'), 'invalid_job_wait')

    def test_empty_param_without_value_drops_to_legacy_get(self):
        # Python parse_qs with default keep_blank_values=False drops blank keys,
        # so ?after_revision= produces an empty query dict and falls through to legacy GET
        job = self.create_mock_job()
        res = self.request('GET', f'/api/jobs/{job["id"]}?after_revision=')
        self.assertEqual(res.status, 200)
        self.assertEqual(res.json()['revision'], 0)

    def test_boundary_valid_values(self):
        job = self.create_mock_job()
        # wait_ms = 0 is a valid probe
        t0 = time.monotonic()
        res = self.request('GET', f'/api/jobs/{job["id"]}?after_revision=0&wait_ms=0')
        elapsed = time.monotonic() - t0
        self.assertEqual(res.status, 200)
        self.assertLess(elapsed, 0.15)
        self.assertEqual(res.json()['revision'], 0)

        # max safe integer revision boundary with short wait_ms
        res = self.request('GET', f'/api/jobs/{job["id"]}?after_revision=9007199254740991&wait_ms=50')
        self.assertEqual(res.status, 200)
        self.assertEqual(res.json()['revision'], 0)

        # wait_ms = 2000 boundary with immediate completed job
        self.app.jobs.update(job['id'], status='completed')
        t0 = time.monotonic()
        res = self.request('GET', f'/api/jobs/{job["id"]}?after_revision=1&wait_ms=2000')
        elapsed = time.monotonic() - t0
        self.assertEqual(res.status, 200)
        self.assertLess(elapsed, 0.2)

    def test_default_parameter_fallbacks(self):
        job = self.create_mock_job()
        # when after_revision is provided without wait_ms, wait_ms defaults to 1000
        # test with an immediate update so we do not wait 1000ms
        t0 = time.monotonic()
        self.app.jobs.update(job['id'], status='running')
        res = self.request('GET', f'/api/jobs/{job["id"]}?after_revision=0')
        elapsed = time.monotonic() - t0
        self.assertEqual(res.status, 200)
        self.assertEqual(res.json()['revision'], 1)
        self.assertLess(elapsed, 0.2)

        # when wait_ms is provided without after_revision, after_revision defaults to 0
        # revision is 1, which is > 0, so it returns immediately
        res = self.request('GET', f'/api/jobs/{job["id"]}?wait_ms=500')
        self.assertEqual(res.status, 200)
        self.assertEqual(res.json()['revision'], 1)

    # -------------------------------------------------------------------------
    # 6. Host and Origin security on the job wait route
    # -------------------------------------------------------------------------
    def test_valid_host_accepted(self):
        job = self.create_mock_job()
        res = self.request('GET', f'/api/jobs/{job["id"]}?after_revision=0&wait_ms=50',
                           headers={'Host': f'127.0.0.1:{self.port}'})
        self.assertEqual(res.status, 200)

        res = self.request('GET', f'/api/jobs/{job["id"]}?after_revision=0&wait_ms=50',
                           headers={'Host': f'localhost:{self.port}'})
        self.assertEqual(res.status, 200)

    def test_foreign_host_rejected(self):
        job = self.create_mock_job()
        res = self.request('GET', f'/api/jobs/{job["id"]}?after_revision=0&wait_ms=50',
                           headers={'Host': 'evil.attacker.com'})
        self.assertEqual(res.status, 421)
        self.assertEqual(res.json()['error']['code'], 'host_not_allowed')

    def test_foreign_origin_rejected(self):
        job = self.create_mock_job()
        res = self.request('GET', f'/api/jobs/{job["id"]}?after_revision=0&wait_ms=50',
                           headers={'Origin': 'http://evil.attacker.com'})
        self.assertEqual(res.status, 403)
        self.assertEqual(res.json()['error']['code'], 'origin_not_allowed')

    # -------------------------------------------------------------------------
    # 7. Server / JobManager shutdown wakes waiting HTTP requests
    # -------------------------------------------------------------------------
    def test_shutdown_wakes_waiting_requests(self):
        with tempfile.TemporaryDirectory(prefix='hangar-shutdown-test-') as tmp:
            d_dir = Path(tmp) / 'data'
            sub_app = App(root=ROOT, data_dir=d_dir, web_dir=ROOT / 'web')
            sub_server = create_server(sub_app, host='127.0.0.1', port=0)
            shost, sport = sub_server.server_address[:2]
            sthread = threading.Thread(
                target=sub_server.serve_forever,
                kwargs={'poll_interval': 0.05},
                daemon=True
            )
            sthread.start()

            try:
                job = sub_app.jobs.create('build', {'mock': True})
                result_holder = {}

                def waiter():
                    t0 = time.monotonic()
                    conn = http.client.HTTPConnection(shost, sport, timeout=5)
                    try:
                        conn.request('GET', f'/api/jobs/{job["id"]}?after_revision=0&wait_ms=2000',
                                     headers={'Connection': 'close', 'Host': f'{shost}:{sport}'})
                        r = conn.getresponse()
                        result_holder['status'] = r.status
                        result_holder['elapsed'] = time.monotonic() - t0
                        result_holder['data'] = json.loads(r.read().decode('utf-8'))
                    finally:
                        conn.close()

                wthread = threading.Thread(target=waiter)
                wthread.start()

                time.sleep(0.06)
                # Shutdown the job manager; queued jobs are marked failed with server_shutdown
                sub_app.jobs.shutdown()
                wthread.join(timeout=3)

                self.assertFalse(wthread.is_alive(), 'Shutdown waiter thread did not exit')
                self.assertIn('status', result_holder)
                self.assertEqual(result_holder['status'], 200)
                self.assertLess(result_holder['elapsed'], 0.6)
                data = result_holder['data']
                self.assertEqual(data['status'], 'failed')
                err = data.get('error') or {}
                self.assertEqual(err.get('code'), 'server_shutdown')
            finally:
                try:
                    sub_server.shutdown()
                except Exception:
                    pass
                try:
                    sub_server.server_close()
                finally:
                    sub_app.close()
                    sthread.join(timeout=3)


if __name__ == '__main__':
    unittest.main(verbosity=2)
