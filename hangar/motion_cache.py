"""Bounded motion index; artifacts use the existing 4GiB/64-job cache policy."""
from pathlib import Path
import threading
from .util import atomic_write_json, read_json, sha256_file, now_iso


class MotionCache:
    def __init__(self, path, limit=256):
        self.path = Path(path)
        self.limit = limit
        self.lock = threading.RLock()
        self.revision = 0
        saved = read_json(self.path, {})
        entries = saved.get('entries', {}) if isinstance(saved, dict) else {}
        self.rows = dict(list((entries if isinstance(entries, dict) else {}).items())[-limit:])
        self.rows = {key:value for key,value in self.rows.items() if isinstance(value, dict)}

    def get(self, key):
        with self.lock:
            return dict(self.rows.get(key, {}))

    def put(self, key, **entry):
        with self.lock:
            self.rows.pop(key, None)
            self.rows[key] = {**entry, 'updated_at':now_iso()}
            self.rows = dict(list(self.rows.items())[-self.limit:])
            self.revision += 1
            atomic_write_json(self.path, {'schema':'h11-motion-cache-1','entries':self.rows})

    @staticmethod
    def completed_job(app, entry, mode):
        job = app.jobs.find(entry.get('job_id')) if entry.get('job_id') else None
        if not job or job.get('status') != 'completed':
            return None
        result = job.get('result') or {}
        from .material_jobs import materials_cacheable
        proof = result.get('native_dynamics') or {}
        if proof.get('motion_processing', {}).get('mode') != mode or not materials_cacheable(result.get('materials')):
            return None
        artifact = result.get('artifact') or {}
        try:
            path = Path(artifact['path'])
            if path.resolve().parent != app.engine.job_output_dir(job['id']).resolve():
                return None
            if sha256_file(path) != artifact['sha256']:
                return None
        except (OSError,KeyError,ValueError):
            return None
        return job
