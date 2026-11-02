import copy
import logging
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import i18n
from .errors import Conflict, HangarError, NotFound
from .util import atomic_write_json, new_id, now_iso, read_json

i18n.register('jobs', {
    'zh-CN': {
        'queued': '已加入队列。',
        'started': '任务已开始。',
        'notFound': '任务不存在或已被清理。',
        'serverStopping': '服务正在关闭。',
        'submitClosed': '服务正在关闭，无法启动新任务。',
        'notCancellable': '该任务已经结束，无法取消。',
        'cancelRequested': '已请求取消，正在停止当前进程…',
        'cancelled': '任务已取消。',
        'workerCrashed': '后台任务异常终止。请查看错误详情与日志。',
        'shutdownQueued': '服务关闭，排队任务已取消。',
        'shutdownNotStarted': 'HANGAR 关闭时任务尚未开始。',
        'restartInterrupted': 'HANGAR 服务在任务运行期间重启；任务已终止，请重新发起。',
        'restartMarkedFailed': '服务重启时任务仍在运行，已标记为失败。',
    },
    'en': {
        'queued': 'Queued.',
        'started': 'The job has started.',
        'notFound': 'The job does not exist or was cleaned up.',
        'serverStopping': 'The service is shutting down.',
        'submitClosed': 'The service is shutting down; new jobs cannot start.',
        'notCancellable': 'The job already finished and cannot be cancelled.',
        'cancelRequested': 'Cancellation requested; stopping the current process...',
        'cancelled': 'The job was cancelled.',
        'workerCrashed': 'The background job terminated abnormally. See the error details and log.',
        'shutdownQueued': 'The service is shutting down; the queued job was cancelled.',
        'shutdownNotStarted': 'HANGAR shut down before the job started.',
        'restartInterrupted': 'HANGAR restarted while the job was running; the job was stopped. Please start it again.',
        'restartMarkedFailed': 'The job was still running when the service restarted; it has been marked as failed.',
    },
})

log = logging.getLogger('hangar.jobs')
JOB_FILE = re.compile(r'^[0-9a-f]{32}\.json$')
TERMINAL = ('completed', 'failed', 'cancelled')
ACTIVE = ('queued', 'running')
CANCELLED = 'cancelled'


def new_job(kind, request):
    stamp = now_iso()
    return {
        'id': new_id(),
        'kind': kind,
        'status': 'queued',
        'stage': 'queued',
        'message': i18n.t('jobs.queued'),
        'progress': 0,
        'progress_fraction': 0.0,
        'created_at': stamp,
        'updated_at': stamp,
        'started_at': None,
        'finished_at': None,
        'request': request or {},
        'result': None,
        'error': None,
        'partial': False,
        'cached': False,
        'cancel_requested': False,
        'revision': 0,
    }


class JobManager:
    # A batch coordinator, the packet build it is currently waiting on, and one
    # spare slot so the studio can still queue a manual build during a batch.
    def __init__(self, jobs_dir, max_workers=3):
        self.jobs_dir = Path(jobs_dir)
        self.jobs_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._changed = threading.Condition(self._lock)
        self._jobs = {}
        self._executor = ThreadPoolExecutor(max_workers=max_workers,
                                            thread_name_prefix='hangar-job')
        self._closing = False
        self._load()

    @staticmethod
    def _public(job):
        """Wire shape: progress is 0..1 for the studio; percent stays explicit."""
        public = copy.deepcopy(job)
        percent = public.get('progress')
        if isinstance(percent, (int, float)) and not isinstance(percent, bool):
            percent = max(0, min(100, int(round(percent))))
            public['progress_percent'] = percent
            public['progress_fraction'] = round(percent / 100.0, 4)
            public['progress'] = public['progress_fraction']
        return public

    # ------------------------------------------------------------------ load
    def _load(self):
        rows = []
        for path in self.jobs_dir.iterdir():
            if not path.is_file() or not JOB_FILE.fullmatch(path.name):
                continue
            data = read_json(path, None)
            if isinstance(data, dict) and data.get('id'):
                rows.append(data)
        rows.sort(key=lambda j: j.get('updated_at') or '', reverse=True)
        for data in rows[:300]:
            if data.get('status') in ACTIVE:
                data.update(status='failed', stage='interrupted',
                            message=i18n.t('jobs.restartInterrupted'),
                            finished_at=now_iso(),
                            error={'code': 'server_restarted',
                                   'message': i18n.t('jobs.restartMarkedFailed'),
                                   'details': {'previous_stage': data.get('stage')}})
                data['updated_at'] = data['finished_at']
                atomic_write_json(self.jobs_dir / f"{data['id']}.json", data)
            self._jobs[data['id']] = data
        if rows:
            log.info('loaded %d persisted jobs', len(rows))

    # --------------------------------------------------------------- persist
    def _path(self, job_id):
        return self.jobs_dir / f'{job_id}.json'

    def _persist(self, job):
        atomic_write_json(self._path(job['id']), job)

    def create(self, kind, request):
        job = new_job(kind, request)
        with self._lock:
            self._jobs[job['id']] = job
            self._persist(job)
        return self._public(job)

    def update(self, job_id, **fields):
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            progress = fields.get('progress', job.get('progress'))
            if isinstance(progress, (int, float)):
                progress = max(0, min(100, int(round(progress))))
                fields['progress'] = progress
                fields['progress_fraction'] = round(progress / 100.0, 4)
            job.update(fields)
            job['updated_at'] = now_iso()
            job['revision'] = job.get('revision', 0) + 1
            self._persist(job)
            self._changed.notify_all()
            return self._public(job)

    def get(self, job_id):
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise NotFound(i18n.t('jobs.notFound'), {'job_id': job_id})
            return self._public(job)

    def find(self, job_id):
        with self._lock:
            job = self._jobs.get(job_id)
            return self._public(job) if job else None

    def wait_for_update(self, job_id, revision, timeout):
        """Wait for progress or cancellation without blocking job writers."""
        with self._changed:
            if job_id not in self._jobs:
                raise NotFound(i18n.t('jobs.notFound'), {'job_id': job_id})
            def changed():
                job = self._jobs[job_id]
                return self._closing or job.get('status') in TERMINAL or job.get('revision', 0) > revision
            self._changed.wait_for(changed, timeout=timeout)
            job = self._jobs[job_id]
            if self._closing and job.get('status') not in TERMINAL:
                raise HangarError('server_stopping', i18n.t('jobs.serverStopping'), 503)
            return self._public(job)

    def list(self, limit=30):
        with self._lock:
            rows = sorted(self._jobs.values(),
                          key=lambda j: j.get('created_at') or '', reverse=True)
            return [self._public(j) for j in rows[:limit]]

    def summaries(self, limit=30):
        result = []
        for job in self.list(limit):
            res = job.get('result') or {}
            summary = {
                'id': job['id'], 'kind': job['kind'], 'status': job['status'],
                'stage': job['stage'], 'message': job.get('message'),
                'progress': job.get('progress'),
                'progress_percent': job.get('progress_percent'),
                'partial': job.get('partial', False),
                'cancel_requested': bool(job.get('cancel_requested')),
                'created_at': job.get('created_at'),
                'updated_at': job.get('updated_at'),
                'finished_at': job.get('finished_at'),
                'model_url': res.get('model_url'),
                'model_ue_url': res.get('model_ue_url'),
                'error': (job.get('error') or {}).get('message')
                if isinstance(job.get('error'), dict) else None,
            }
            result.append(summary)
        return result

    def request_cancel(self, job_id):
        """Ask a running job to stop.

        The runner polls this flag and kills its own worker tree, so nothing is
        reported cancelled until the child process is really gone.
        """
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                raise NotFound(i18n.t('jobs.notFound'), {'job_id': job_id})
            if job.get('status') not in ACTIVE:
                raise Conflict('job_not_cancellable', i18n.t('jobs.notCancellable'),
                               {'status': job.get('status')})
            job['cancel_requested'] = True
            job['message'] = i18n.t('jobs.cancelRequested')
            job['updated_at'] = now_iso()
            job['revision'] = job.get('revision', 0) + 1
            self._persist(job)
            self._changed.notify_all()
            return self._public(job)

    def should_cancel(self, job_id):
        with self._lock:
            job = self._jobs.get(job_id)
            return bool(job and job.get('cancel_requested'))

    def finish_cancelled(self, job_id, message=None, result=None):
        if message is None:
            message = i18n.t('jobs.cancelled')
        return self.update(job_id, status=CANCELLED, stage='cancelled',
                           progress=100, message=message, partial=True,
                           result=result, finished_at=now_iso(),
                           error={'code': 'cancelled', 'message': message,
                                  'details': {}})

    def active(self, kind=None):
        with self._lock:
            return [copy.deepcopy(j)
                    for j in self._jobs.values()
                    if j.get('status') in ACTIVE and (kind is None or j.get('kind') == kind)]

    # ------------------------------------------------------------------ run
    def submit(self, job_id, fn):
        with self._lock:
            if self._closing:
                raise HangarError('server_stopping', i18n.t('jobs.submitClosed'), 503)
            # 作业线程按提交请求的语言产出进度与失败文案。
            lang = i18n.current_lang()
            self._executor.submit(self._run, job_id, fn, lang)

    def _run(self, job_id, fn, lang=None):
        i18n.set_current_lang(lang)
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return
            job.update(status='running', stage='starting', started_at=now_iso(),
                       message=i18n.t('jobs.started'))
            job['updated_at'] = now_iso()
            job['revision'] = job.get('revision', 0) + 1
            self._persist(job)
            self._changed.notify_all()
        try:
            fn(job_id)
        except HangarError as ex:
            self.update(job_id, status='failed', stage='failed', progress=100,
                        message=ex.message, error=ex.envelope()['error'],
                        finished_at=now_iso())
            log.warning('job %s failed: %s (%s)', job_id, ex.code, ex.message)
        except Exception as ex:  # noqa: BLE001 - boundary of a background worker
            log.exception('job %s crashed', job_id)
            self.update(job_id, status='failed', stage='failed', progress=100,
                        message=i18n.t('jobs.workerCrashed'),
                        finished_at=now_iso(),
                        error={'code': 'internal_error',
                               'message': f'{type(ex).__name__}: {ex}',
                               'details': {}})

    def shutdown(self, wait=False):
        with self._lock:
            self._closing = True
            queued = [j for j in self._jobs.values() if j.get('status') == 'queued']
            for job in queued:
                job.update(status='failed', stage='cancelled',
                           message=i18n.t('jobs.shutdownQueued'),
                           finished_at=now_iso(),
                           error={'code': 'server_shutdown',
                                  'message': i18n.t('jobs.shutdownNotStarted'),
                                  'details': {}})
                job['updated_at'] = now_iso()
                job['revision'] = job.get('revision', 0) + 1
                self._persist(job)
            self._changed.notify_all()
        self._executor.shutdown(wait=wait, cancel_futures=True)
