"""H03 completion notifications and H04 cancellation share one revision stream."""
import tempfile
import threading
import unittest
from pathlib import Path
from hangar.jobs import JobManager
from hangar.app import App


class CancelNotifications(unittest.TestCase):
    def test_cancel_request_and_terminal_state_notify_and_survive_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            jobs = JobManager(Path(tmp))
            try:
                created = jobs.create('export-selection', {})
                snapshots = []
                entered = threading.Event()
                def wait():
                    entered.set()
                    snapshots.append(jobs.wait_for_update(created['id'], created['revision'], 2))
                thread = threading.Thread(target=wait)
                thread.start()
                self.assertTrue(entered.wait(1))
                requested = jobs.request_cancel(created['id'])
                thread.join(1)
                self.assertFalse(thread.is_alive())
                self.assertTrue(snapshots[0]['cancel_requested'])
                self.assertGreater(requested['revision'], created['revision'])
                final = jobs.finish_cancelled(created['id'])
                self.assertGreater(final['revision'], requested['revision'])
                self.assertEqual(jobs.wait_for_update(created['id'], final['revision'], 1)['status'], 'cancelled')
            finally:
                jobs.shutdown()
            reopened = JobManager(Path(tmp))
            try:
                saved = reopened.get(created['id'])
                self.assertEqual(saved['status'], 'cancelled')
                self.assertEqual(saved['revision'], final['revision'])
            finally:
                reopened.shutdown()

    def test_worker_start_notifies_before_long_running_work(self):
        with tempfile.TemporaryDirectory() as tmp:
            jobs = JobManager(Path(tmp))
            release = threading.Event()
            try:
                created = jobs.create('build', {})
                jobs.submit(created['id'], lambda _: release.wait(2))
                started = jobs.wait_for_update(created['id'], created['revision'], .5)
                self.assertEqual(started['status'], 'running')
                self.assertGreater(started['revision'], created['revision'])
            finally:
                release.set()
                jobs.shutdown(wait=True)


class PacketCancellation(unittest.TestCase):
    def exercise(self, race=False):
        with tempfile.TemporaryDirectory() as tmp:
            app = App(root=Path(__file__).resolve().parents[1], data_dir=Path(tmp))
            try:
                parent = app.jobs.create('export-selection', {})
                child = app.jobs.create('packet', {})
                app.jobs.request_cancel(parent['id'])
                app._create_build_job = lambda *a, **k: (child, True)
                if race:
                    original = app.jobs.request_cancel
                    def finish_first(ident):
                        app.jobs.finish_cancelled(ident)
                        return original(ident)
                    app.jobs.request_cancel = finish_first
                else:
                    app.jobs.finish_cancelled(child['id'])
                result, details = app._run_packet_build(parent['id'], {'parts':{}}, [], 1, 1)
                self.assertIsNone(result)
                self.assertEqual(details['status'], 'cancelled')
            finally:
                app.close()

    def test_already_cancelled_packet_does_not_fail_parent(self):
        self.exercise()

    def test_packet_finishing_during_cancel_request_does_not_fail_parent(self):
        self.exercise(race=True)


if __name__ == '__main__':
    unittest.main()
