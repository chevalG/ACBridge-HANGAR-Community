"""Black-box acceptance tests for the ACBridge HANGAR local REST service.

These tests intentionally import nothing from ``hangar``.  They exercise the
HTTP contract in docs/PLAN.md and do not mirror implementation details.

Run modes
---------
Managed (default), launches ``python -m hangar`` from this repository with a
private temporary directory and restarts it to prove durable state::

    runtime/python.exe tests/test_acceptance.py -v

Attached, against an already running service; restart-based durability tests
are skipped because this suite must not kill a service it does not own::

    HANGAR_TEST_URL=http://127.0.0.1:8765 runtime/python.exe tests/test_acceptance.py -v

Useful environment variables
----------------------------
HANGAR_TEST_URL            attach to an existing service instead of launching
HANGAR_PYTHON              interpreter used to launch the service
HANGAR_TEST_HEALTH_TIMEOUT seconds to wait for /api/health (default 90)
HANGAR_TEST_JOB_TIMEOUT    seconds to wait for an async job (default 120)
HANGAR_TEST_REQUIRE_SERVER 1 makes "service unavailable" an error, not a skip

If ``hangar`` is not importable yet, the module skips with a loud NOT RUN
banner.  A skip is never a pass.
"""

from __future__ import annotations

import copy
import http.client
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.parse
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

HTTP_TIMEOUT = float(os.environ.get("HANGAR_TEST_HTTP_TIMEOUT", "30"))
HEALTH_TIMEOUT = float(os.environ.get("HANGAR_TEST_HEALTH_TIMEOUT", "90"))
JOB_TIMEOUT = float(os.environ.get("HANGAR_TEST_JOB_TIMEOUT", "120"))

SLOTS = ("HD", "BD", "AM", "LG")
PRESET_SLOTS = SLOTS

DEFAULT_CONFIG = {
    "parts": {slot: None for slot in PRESET_SLOTS},
    "clip_ids": [],
    "weapons": {},
    "view": {"mode": "orbit"},
}

# Presets are portable loadout snapshots, not references that must exist in the
# currently imported catalog.  When the service has no last_config yet, use a
# complete, well-formed loadout so saving a preset is still testable offline.
SYNTHETIC_CONFIG = {
    "parts": {
        "HD": "HD_M_9001",
        "BD": "BD_M_9002",
        "AM": "AM_M_9003",
        "LG": "LG_M_9004",
    },
    "clip_ids": [],
    "weapons": {},
    "view": {"mode": "orbit"},
}

_UNSET = object()


# ---------------------------------------------------------------------------
# HTTP plumbing
# ---------------------------------------------------------------------------


class Reply:
    """A fully read HTTP response."""

    def __init__(self, method, path, status, reason, headers, body):
        self.method = method
        self.path = path
        self.status = status
        self.reason = reason
        self.headers = list(headers)
        self.body = body

    def header(self, name):
        wanted = name.lower()
        for key, value in self.headers:
            if key.lower() == wanted:
                return value
        return None

    @property
    def content_type(self):
        return (self.header("content-type") or "").split(";")[0].strip().lower()

    def text(self):
        return self.body.decode("utf-8", "replace")

    def snippet(self, limit=700):
        text = self.text()
        if len(text) > limit:
            text = text[:limit] + "...<truncated>"
        return f"{self.method} {self.path} -> {self.status} {self.reason} " \
               f"ct={self.header('content-type')!r} body={text!r}"

    def json(self):
        try:
            return json.loads(self.body.decode("utf-8"))
        except Exception as exc:  # noqa: BLE001 - surfaced as a test failure
            raise AssertionError(
                f"expected a JSON body from {self.method} {self.path}, got "
                f"{self.snippet()}"
            ) from exc


class HttpClient:
    """Minimal HTTP client that sends raw paths, including encoded traversal."""

    def __init__(self, base_url, timeout=None):
        parts = urllib.parse.urlsplit(base_url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise ValueError(f"unsupported service URL: {base_url!r}")
        self.scheme = parts.scheme
        self.host = parts.hostname
        self.port = parts.port or (443 if parts.scheme == "https" else 80)
        self.prefix = parts.path.rstrip("/")
        self.timeout = timeout or HTTP_TIMEOUT

    @property
    def origin(self):
        return f"{self.scheme}://{self.host}:{self.port}"

    def request(self, method, path, payload=_UNSET, body=None, headers=None,
                timeout=None):
        if not path.startswith("/"):
            raise ValueError(f"request path must start with '/': {path!r}")
        target = self.prefix + path
        request_headers = {
            "Accept": "application/json, text/html;q=0.9, */*;q=0.1",
            "Accept-Encoding": "identity",
            "Connection": "close",
            "User-Agent": "HANGAR-QA/1.0",
        }
        if headers:
            request_headers.update(headers)
        if payload is not _UNSET:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            request_headers["Content-Type"] = "application/json; charset=utf-8"

        connection_class = (
            http.client.HTTPSConnection if self.scheme == "https"
            else http.client.HTTPConnection
        )
        connection = connection_class(
            self.host, self.port, timeout=timeout or self.timeout
        )
        try:
            connection.request(method, target, body=body, headers=request_headers)
            raw = connection.getresponse()
            data = raw.read()
            return Reply(method, target, raw.status, raw.reason,
                         raw.getheaders(), data)
        finally:
            connection.close()


# ---------------------------------------------------------------------------
# Service lifecycle
# ---------------------------------------------------------------------------


class Service:
    """Owns either an attached URL or a launched, restartable subprocess."""

    def __init__(self):
        self.base_url = None
        self.external = False
        self._tmp = None
        self._data_dir = None
        self._proc = None
        self._log_path = None
        self._log_handle = None
        self._interpreter = None
        self._help = ""
        self._data_flag = None
        self._no_browser = True
        self._attempts = 0

    # -- public ---------------------------------------------------------

    @property
    def origin(self):
        return HttpClient(self.base_url).origin

    @property
    def data_dir(self):
        return self._data_dir

    def client(self):
        if not self.base_url:
            raise RuntimeError("service is not started")
        return HttpClient(self.base_url)

    def start(self):
        url = os.environ.get("HANGAR_TEST_URL", "").strip()
        if url:
            self.external = True
            self.base_url = url.rstrip("/")
            self.client()  # validates URL shape
            if not self._wait_healthy(20):
                raise RuntimeError(
                    f"HANGAR_TEST_URL={url!r} did not return "
                    f"{{'ok': true}} from /api/health within 20s"
                )
            print(f"[QA] attached to existing service {self.base_url}",
                  file=sys.stderr)
            return

        self._tmp = Path(tempfile.mkdtemp(prefix="hangar-qa-"))
        self._data_dir = self._tmp / "data"
        self._data_dir.mkdir(parents=True, exist_ok=True)

        self._interpreter = self._find_interpreter()
        if not self._interpreter:
            self._discard_tmp()
            message = (
                "TESTS NOT RUN: the 'hangar' package is not importable from "
                f"{ROOT}. No HANGAR_TEST_URL was supplied, so the acceptance "
                "suite cannot reach a REST service."
            )
            print("[QA] " + message, file=sys.stderr)
            if os.environ.get("HANGAR_TEST_REQUIRE_SERVER") == "1":
                raise RuntimeError(message)
            raise unittest.SkipTest(message)

        self._help = self._probe_help()
        self._data_flag = self._detect_data_flag()
        self._no_browser = ("--no-browser" in self._help) if self._help else True
        self._launch()
        print(
            f"[QA] launched service {self.base_url} with {self._interpreter}; "
            f"data dir {self._data_dir} "
            f"(flag={self._data_flag or 'none'})",
            file=sys.stderr,
        )

    def restart(self):
        if self.external:
            raise unittest.SkipTest(
                "restart durability requires the managed server; "
                "unset HANGAR_TEST_URL to run it"
            )
        self._stop_process()
        self._launch()

    def stop(self):
        self._stop_process()
        self._discard_tmp()

    def _discard_tmp(self):
        """Remove the private state dir, tolerating Windows shutdown races."""
        if self._tmp is None:
            return
        for _ in range(4):
            shutil.rmtree(self._tmp, ignore_errors=True)
            if not self._tmp.exists():
                break
            time.sleep(0.2)
        self._tmp = None

    # -- interpreter and flags -----------------------------------------

    def _env(self):
        env = os.environ.copy()
        env["PYTHONUTF8"] = "1"
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        pythonpath = [str(ROOT)]
        if env.get("PYTHONPATH"):
            pythonpath.append(env["PYTHONPATH"])
        env["PYTHONPATH"] = os.pathsep.join(pythonpath)
        tools = str(ROOT / "runtime" / "tools")
        env["PATH"] = tools + os.pathsep + env.get("PATH", "")
        if self._data_dir is not None:
            # Accepted spellings used by the service and its worker.
            env["HANGAR_DATA"] = str(self._data_dir)
            env["HANGAR_DATA_DIR"] = str(self._data_dir)
            env["HANGAR_STATE_DIR"] = str(self._data_dir)
        return env

    def _find_interpreter(self):
        candidates = [
            os.environ.get("HANGAR_PYTHON"),
            str(ROOT / "runtime" / "python.exe"),
            sys.executable,
            shutil.which("python"),
        ]
        seen = set()
        for candidate in candidates:
            if not candidate:
                continue
            if candidate in seen:
                continue
            seen.add(candidate)
            if not Path(candidate).is_file():
                continue
            try:
                completed = subprocess.run(
                    [candidate, "-B", "-c",
                     "import hangar, sys; print(hangar.__file__)"],
                    cwd=str(ROOT),
                    env=self._env(),
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=45,
                )
            except Exception:  # noqa: BLE001 - try the next interpreter
                continue
            if completed.returncode == 0 and "hangar" in (completed.stdout or "").lower():
                return candidate
        return None

    def _probe_help(self):
        try:
            completed = subprocess.run(
                [self._interpreter, "-B", "-m", "hangar", "--help"],
                cwd=str(ROOT),
                env=self._env(),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=45,
            )
        except Exception:  # noqa: BLE001 - launch without help heuristics
            return ""
        if completed.returncode not in (0, 2):
            return ""
        text = (completed.stdout or "") + (completed.stderr or "")
        return text if "--" in text else ""

    def _detect_data_flag(self):
        for match in re.finditer(r"--([a-z0-9][a-z0-9-]*)", self._help):
            name = match.group(1)
            if name == "no-browser":
                continue
            if any(token in name for token in ("data", "state", "storage")):
                return "--" + name
        return None

    # -- launch ----------------------------------------------------------

    def _free_port(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            return sock.getsockname()[1]

    def _launch(self):
        attempts = [True]
        if self._no_browser or self._data_flag:
            attempts.append(False)  # minimal argv fallback for older CLIs

        last_detail = ""
        for use_flags in attempts:
            if not use_flags and not (self._no_browser or self._data_flag):
                continue
            port = self._free_port()
            self._attempts += 1
            command = [self._interpreter, "-B", "-m", "hangar",
                       "--port", str(port)]
            if use_flags:
                if self._no_browser:
                    command.append("--no-browser")
                if self._data_flag:
                    command.extend([self._data_flag, str(self._data_dir)])

            outcome, detail = self._attempt(port, command, self._attempts)
            if outcome == "ok":
                return
            last_detail = detail
            if outcome == "timeout":
                raise RuntimeError(
                    "service process stayed alive but /api/health never became "
                    f"ready: {' '.join(command)}\n{detail}"
                )
            print(
                f"[QA] launch attempt {self._attempts} exited before readiness; "
                "trying a minimal command line",
                file=sys.stderr,
            )

        if ("No module named hangar.__main__" in last_detail
                or "cannot be directly executed" in last_detail):
            self.stop()
            message = (
                "TESTS NOT RUN: the 'hangar' package exists but has no "
                "executable __main__ yet, so no REST service can be started."
            )
            print("[QA] " + message, file=sys.stderr)
            if os.environ.get("HANGAR_TEST_REQUIRE_SERVER") == "1":
                raise RuntimeError(message)
            raise unittest.SkipTest(message)

        raise RuntimeError(
            "unable to start the HANGAR service with "
            f"{self._interpreter} -m hangar (cwd={ROOT}). "
            "Backend may be incomplete or unsupported flags were used.\n"
            + last_detail
        )

    def _attempt(self, port, command, attempt):
        log_path = self._tmp / f"server-{attempt}.log"
        log_handle = open(log_path, "wb")
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        proc = subprocess.Popen(
            command,
            cwd=str(ROOT),
            env=self._env(),
            stdin=subprocess.DEVNULL,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            creationflags=creationflags,
        )
        self._proc = proc
        self._log_path = log_path
        self._log_handle = log_handle
        self.base_url = f"http://127.0.0.1:{port}"

        deadline = time.monotonic() + HEALTH_TIMEOUT
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                tail = self._log_tail()
                self._reset_process()
                return "exited", tail
            if self._wait_healthy(2):
                return "ok", ""
            time.sleep(0.25)

        tail = self._log_tail()
        self._stop_process()
        return "timeout", tail

    def _wait_healthy(self, timeout):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                reply = self.client().request("GET", "/api/health", timeout=2)
                if reply.status == 200:
                    data = reply.json()
                    if isinstance(data, dict) and data.get("ok") is True:
                        return True
            except Exception:  # noqa: BLE001 - retry until the deadline
                pass
            time.sleep(0.2)
        return False

    def _reset_process(self):
        if self._log_handle is not None:
            try:
                self._log_handle.close()
            except OSError:
                pass
        self._log_handle = None
        self._proc = None
        self.base_url = None

    def _stop_process(self):
        proc = self._proc
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
        if self._log_handle is not None:
            try:
                self._log_handle.close()
            except OSError:
                pass
        self._log_handle = None
        self._proc = None
        self.base_url = None

    def _log_tail(self, limit=4000):
        if not self._log_path:
            return ""
        try:
            return self._log_path.read_text(encoding="utf-8", errors="replace")[-limit:]
        except OSError:
            return ""


SERVICE = Service()


def setUpModule():
    SERVICE.start()


def tearDownModule():
    SERVICE.stop()


# ---------------------------------------------------------------------------
# Shared assertions and helpers
# ---------------------------------------------------------------------------


class HangarTestCase(unittest.TestCase):
    service = SERVICE

    def setUp(self):
        self.service = SERVICE
        self.c = SERVICE.client()
        self._preset_ids = []
        self.addCleanup(self._cleanup_presets)

    # -- convenience ----------------------------------------------------

    def get(self, path, headers=None):
        return self.c.request("GET", path, headers=headers)

    def post(self, path, payload=_UNSET, headers=None):
        return self.c.request("POST", path, payload=payload, headers=headers)

    def put(self, path, payload=_UNSET, headers=None):
        return self.c.request("PUT", path, payload=payload, headers=headers)

    def delete(self, path, headers=None):
        return self.c.request("DELETE", path, headers=headers)

    def state(self):
        reply = self.get("/api/state")
        self.assertEqual(reply.status, 200,
                         f"/api/state must be 200: {reply.snippet()}")
        data = reply.json()
        self.assertIsInstance(data, dict, "/api/state must be a JSON object")
        return data

    # -- assertions -----------------------------------------------------

    def assert_error(self, reply, allowed=(400,), note=""):
        prefix = f"{note}: " if note else ""
        self.assertIn(
            reply.status,
            allowed,
            f"{prefix}expected error status {allowed}, got {reply.snippet()}",
        )
        self.assertIn(
            "application/json",
            reply.content_type,
            f"{prefix}API errors must be JSON: {reply.snippet()}",
        )
        data = reply.json()
        self.assertIsInstance(data, dict,
                              f"{prefix}error body must be a JSON object")
        error = data.get("error")
        self.assertIsInstance(
            error, dict,
            f"{prefix}error body must contain an 'error' object: {reply.snippet()}",
        )
        code = error.get("code")
        message = error.get("message")
        self.assertTrue(
            (isinstance(code, str) and code.strip()) or
            (isinstance(code, int) and not isinstance(code, bool)),
            f"{prefix}error.code must be a non-empty string (or integer): "
            f"{reply.snippet()}",
        )
        self.assertTrue(
            isinstance(message, str) and message.strip(),
            f"{prefix}error.message must be a non-empty string: {reply.snippet()}",
        )
        return error

    def assert_no_disclosure(self, reply, needles, label):
        lowered = reply.text().lower()
        for needle in needles:
            self.assertNotIn(
                needle.lower(),
                lowered,
                f"{label}: response leaked {needle!r}: {reply.snippet()}",
            )

    # -- preset helpers -------------------------------------------------

    def _unique(self, prefix):
        return f"QA {prefix} {uuid.uuid4().hex[:10]}"

    def _config(self):
        state = self.state()
        config = state.get("last_config")
        if isinstance(config, dict):
            parts = config.get("parts")
            if isinstance(parts, dict) and all(
                    str(parts.get(slot) or "").strip()
                    for slot in PRESET_SLOTS):
                return copy.deepcopy(config)
        return copy.deepcopy(SYNTHETIC_CONFIG)

    def create_preset(self, name=None, config=None):
        name = name or self._unique("preset")
        config = copy.deepcopy(config if config is not None else self._config())
        reply = self.post("/api/presets", {"name": name, "config": config})
        self.assertIn(
            reply.status, (200, 201),
            f"valid preset save must succeed: {reply.snippet()}",
        )
        preset = reply.json()
        self.assertIsInstance(preset, dict)
        self.assertIn("id", preset,
                      f"created preset must expose an id: {reply.snippet()}")
        self.assertEqual(preset.get("name"), name)
        self.assertEqual(preset.get("config"), config,
                         "created preset must echo the saved config")
        self._preset_ids.append(preset["id"])
        return preset

    def find_preset(self, state, preset_id):
        wanted = str(preset_id)
        for preset in state.get("presets", []):
            if str(preset.get("id")) == wanted:
                return preset
        return None

    def delete_preset(self, preset_id):
        path = "/api/presets/" + urllib.parse.quote(str(preset_id), safe="")
        reply = self.delete(path)
        self.assertIn(
            reply.status, (200, 204),
            f"deleting an existing preset must succeed: {reply.snippet()}",
        )
        try:
            self._preset_ids.remove(preset_id)
        except ValueError:
            pass
        return reply

    def _cleanup_presets(self):
        for preset_id in list(getattr(self, "_preset_ids", [])):
            try:
                path = "/api/presets/" + urllib.parse.quote(
                    str(preset_id), safe="")
                self.c.request("DELETE", path, timeout=10)
            except Exception:  # noqa: BLE001 - best-effort cleanup
                pass
            try:
                self._preset_ids.remove(preset_id)
            except ValueError:
                pass

    # -- job helpers ----------------------------------------------------

    def poll_job(self, job_id, timeout=None):
        timeout = JOB_TIMEOUT if timeout is None else timeout
        path = "/api/jobs/" + urllib.parse.quote(str(job_id), safe="")
        deadline = time.monotonic() + timeout
        last = None
        while time.monotonic() < deadline:
            reply = self.get(path)
            self.assertEqual(
                reply.status, 200,
                f"polling known job {job_id!r} failed: {reply.snippet()}",
            )
            job = reply.json()
            self.assertIsInstance(job, dict)
            self.assertEqual(str(job.get("id")), str(job_id),
                             "job document must echo its id")
            status = job.get("status")
            self.assertIn(
                status, ("queued", "running", "completed", "failed"),
                f"unknown job status {status!r}: {reply.snippet()}",
            )
            last = job
            if status in ("completed", "failed"):
                return job
            time.sleep(0.25)
        self.fail(
            f"job {job_id!r} did not reach a terminal status within "
            f"{timeout}s; last={last!r}"
        )

    def assert_job_failed_without_result(self, job, label):
        self.assertEqual(
            job.get("status"), "failed",
            f"{label}: expected a failed job, got {job!r}",
        )
        result = job.get("result")
        self.assertFalse(
            isinstance(result, dict) and result.get("model_url"),
            f"{label}: failed job must not expose a model_url: {job!r}",
        )
        error = job.get("error")
        self.assertTrue(error, f"{label}: failed job must explain why: {job!r}")

    def import_must_not_succeed(self, payload, label):
        """Import either rejects up front or produces a failed job."""
        reply = self.post("/api/import", payload)
        if reply.status in (400, 422):
            self.assert_error(reply, (400, 422), label)
            return None
        self.assertIn(
            reply.status, (200, 202),
            f"{label}: unexpected import status: {reply.snippet()}",
        )
        data = reply.json()
        self.assertIsInstance(data, dict)
        job_id = data.get("job_id")
        self.assertTrue(
            isinstance(job_id, (str, int)) and str(job_id).strip(),
            f"{label}: accepted import must return job_id: {reply.snippet()}",
        )
        job = self.poll_job(job_id)
        self.assert_job_failed_without_result(job, label)
        return job

    def build_payload(self, clip_ids):
        """Use real catalog parts when present, otherwise deliberate fakes."""
        state = self.state()
        parts = state.get("catalog", {}).get("parts", [])
        chosen = {}
        for slot in SLOTS:
            for part in parts:
                if str(part.get("slot")) == slot and part.get("available") is True:
                    chosen[slot] = part.get("id")
                    break
        if len(chosen) != len(SLOTS):
            chosen = {slot: f"qa-missing-{slot.lower()}" for slot in SLOTS}
        return {"parts": chosen, "clip_ids": list(clip_ids)}


# ---------------------------------------------------------------------------
# Contract surface
# ---------------------------------------------------------------------------


class TestServiceContract(HangarTestCase):
    def test_health_reports_version_and_engine(self):
        reply = self.get("/api/health")
        self.assertEqual(reply.status, 200, reply.snippet())
        self.assertIn("application/json", reply.content_type)
        data = reply.json()
        self.assertIsInstance(data, dict)
        self.assertIs(data.get("ok"), True)
        self.assertTrue(str(data.get("version") or "").strip(),
                        "health must report a non-empty version")
        self.assertTrue(data.get("engine"),
                        "health must report engine information")

    def test_state_document_matches_contract_shape(self):
        data = self.state()
        for key in ("settings", "catalog", "presets", "jobs", "last_config"):
            self.assertIn(key, data, f"/api/state is missing {key!r}")
        self.assertIsInstance(data["settings"], dict)
        self.assertIsInstance(data["presets"], list)
        self.assertIsInstance(data["jobs"], list)

        catalog = data["catalog"]
        self.assertIsInstance(catalog, dict)
        for key in ("parts", "clips", "weapons"):
            self.assertIn(key, catalog, f"catalog is missing {key!r}")
            self.assertIsInstance(catalog[key], list)

        for part in catalog["parts"]:
            self.assertIsInstance(part, dict)
            for key in ("id", "slot", "name", "available", "source"):
                self.assertIn(key, part, f"part row missing {key!r}: {part!r}")
            self.assertIn(part["slot"], SLOTS,
                          f"part row has an invalid slot: {part!r}")
            self.assertTrue(str(part["name"] or "").strip(),
                            f"part row needs a human name: {part!r}")
            self.assertIsInstance(part["available"], bool)

        for clip in catalog["clips"]:
            self.assertIsInstance(clip, dict)
            for key in ("id", "name", "source", "hkx", "compendium"):
                self.assertIn(key, clip, f"clip row missing {key!r}: {clip!r}")
            self.assertTrue(str(clip["name"] or "").strip(),
                            f"clip row needs a human name: {clip!r}")

    def test_root_serves_studio_html(self):
        reply = self.get("/")
        self.assertEqual(reply.status, 200, reply.snippet())
        self.assertEqual(reply.content_type, "text/html",
                         f"/ must serve the studio HTML: {reply.snippet()}")
        body = reply.text().lower()
        self.assertTrue("<!doctype" in body or "<html" in body,
                        "studio shell does not look like HTML")
        self.assertNotIn("traceback (most recent call last)", body)

    def test_unknown_api_path_returns_json_404(self):
        path = "/api/qa-does-not-exist-" + uuid.uuid4().hex
        self.assert_error(self.get(path), (404,),
                          "unknown API route")

    def test_unknown_job_returns_json_404(self):
        path = "/api/jobs/qa-missing-" + uuid.uuid4().hex
        self.assert_error(self.get(path), (404,),
                          "unknown job")

    def test_unsupported_method_returns_json_error(self):
        reply = self.delete("/api/health")
        error = self.assert_error(reply, (404, 405), "unsupported method")
        if reply.status == 405:
            details = error.get("details") if isinstance(error, dict) else None
            advertised = reply.header("allow") or (
                details.get("allow") if isinstance(details, dict) else None
            )
            self.assertTrue(
                advertised,
                "405 should advertise the methods it accepts (Allow header "
                "or error.details.allow)",
            )

    def test_service_survives_invalid_requests(self):
        bad = self.c.request(
            "POST", "/api/presets",
            body=b'{"name": "broken"',
            headers={"Content-Type": "application/json"},
        )
        self.assertIn(bad.status, (400, 422), bad.snippet())
        self.assertEqual(self.get("/api/health").status, 200,
                         "service must stay healthy after malformed input")


# ---------------------------------------------------------------------------
# JSON and schema validation
# ---------------------------------------------------------------------------


class TestJsonAndSchemaErrors(HangarTestCase):
    def test_malformed_json_is_rejected_as_json(self):
        reply = self.c.request(
            "POST", "/api/presets",
            body=b'{"name": "unterminated", "config": {',
            headers={"Content-Type": "application/json"},
        )
        self.assert_error(reply, (400,), "malformed JSON")

    def test_json_array_body_is_rejected(self):
        reply = self.post("/api/presets", [])
        self.assert_error(reply, (400,), "array body")

    def test_json_null_body_is_rejected(self):
        reply = self.post("/api/presets", None)
        self.assert_error(reply, (400,), "null body")

    def test_build_requires_parts(self):
        # 409 is allowed while the catalog is empty: "no parts imported" takes
        # precedence over payload shape.  Either way the request must be refused.
        self.assert_error(self.post("/api/build", {}), (400, 409, 422),
                          "build without parts")

    def test_build_rejects_non_object_parts(self):
        reply = self.post("/api/build", {"parts": ["HD"], "clip_ids": []})
        self.assert_error(reply, (400, 409, 422), "build with array parts")

    def test_import_requires_roots(self):
        self.assert_error(self.post("/api/import", {"mode": "expanded"}),
                          (400,), "import without roots")

    def test_import_rejects_non_path_roots(self):
        reply = self.post("/api/import",
                          {"roots": 123, "mode": "expanded"})
        self.assert_error(reply, (400,), "import with numeric roots")

    def test_import_rejects_unknown_mode(self):
        with tempfile.TemporaryDirectory(prefix="hangar-qa-mode-") as folder:
            reply = self.post("/api/import",
                              {"roots": [folder], "mode": "not-a-mode"})
        self.assert_error(reply, (400, 422), "import with unknown mode")

    def test_preset_requires_non_empty_string_name(self):
        cases = {
            "missing name": {"config": copy.deepcopy(DEFAULT_CONFIG)},
            "blank name": {"name": "   ", "config": copy.deepcopy(DEFAULT_CONFIG)},
            "numeric name": {"name": 17, "config": copy.deepcopy(DEFAULT_CONFIG)},
        }
        for label, payload in cases.items():
            with self.subTest(case=label):
                self.assert_error(self.post("/api/presets", payload),
                                  (400, 422), label)

    def test_preset_requires_object_config(self):
        reply = self.post("/api/presets",
                          {"name": self._unique("bad config"), "config": []})
        self.assert_error(reply, (400, 422), "array config")


# ---------------------------------------------------------------------------
# Import path validation
# ---------------------------------------------------------------------------


class TestImportPathErrors(HangarTestCase):
    def test_relative_root_is_rejected(self):
        reply = self.post("/api/import",
                          {"roots": ["relative-assets"], "mode": "expanded"})
        self.assert_error(reply, (400, 422), "relative import root")

    def test_nonexistent_root_is_rejected(self):
        missing = Path(tempfile.gettempdir()) / ("qa-missing-" + uuid.uuid4().hex)
        self.assertFalse(missing.exists())
        reply = self.post("/api/import",
                          {"roots": [str(missing)], "mode": "expanded"})
        self.assert_error(reply, (400, 422), "nonexistent import root")

    def test_file_root_is_rejected(self):
        with tempfile.NamedTemporaryFile(prefix="hangar-qa-file-",
                                         suffix=".txt", delete=False) as handle:
            path = Path(handle.name)
            handle.write(b"not a directory\n")
        try:
            reply = self.post("/api/import",
                              {"roots": [str(path)], "mode": "expanded"})
        finally:
            try:
                path.unlink()
            except OSError:
                pass
        self.assert_error(reply, (400, 422), "file used as import root")

    def test_empty_string_root_is_rejected(self):
        reply = self.post("/api/import",
                          {"roots": [""], "mode": "expanded"})
        self.assert_error(reply, (400, 422), "empty import root")

    def test_empty_root_list_does_not_produce_success(self):
        self.import_must_not_succeed({"roots": [], "mode": "expanded"},
                                     "empty root list")


# ---------------------------------------------------------------------------
# Missing imported resources / unknown clip
# ---------------------------------------------------------------------------


class TestMissingResources(HangarTestCase):
    def test_empty_directory_import_fails_without_result(self):
        with tempfile.TemporaryDirectory(prefix="hangar-qa-empty-") as folder:
            self.import_must_not_succeed(
                {"roots": [folder], "mode": "expanded"},
                "empty asset directory",
            )

    def test_junk_directory_import_fails_without_result(self):
        with tempfile.TemporaryDirectory(prefix="hangar-qa-junk-") as folder:
            (Path(folder) / "readme.txt").write_text(
                "no engine assets here\n", encoding="utf-8")
            (Path(folder) / "blob.bin").write_bytes(b"\x00" * 64)
            self.import_must_not_succeed(
                {"roots": [folder], "mode": "expanded"},
                "directory without engine resources",
            )

    def test_unknown_clip_is_never_reported_as_exported(self):
        unknown_clip = "qa_unknown_clip_" + uuid.uuid4().hex
        payload = self.build_payload(clip_ids=[unknown_clip])
        reply = self.post("/api/build", payload)
        if reply.status in (400, 409, 422):
            self.assert_error(reply, (400, 409, 422), "unknown clip build")
            return
        self.assertIn(reply.status, (200, 202),
                      f"unexpected build status: {reply.snippet()}")
        data = reply.json()
        job_id = data.get("job_id")
        self.assertTrue(job_id, f"accepted build must return job_id: "
                                f"{reply.snippet()}")
        job = self.poll_job(job_id)

        if job.get("status") == "failed":
            self.assert_job_failed_without_result(job, "unknown clip build")
            return

        # A graceful partial build is acceptable, but it must not claim the
        # unknown clip produced animation, and it must say something went wrong.
        self.assertEqual(job.get("status"), "completed")
        result = job.get("result")
        self.assertIsInstance(result, dict,
                              f"completed job needs a result: {job!r}")
        completion = result.get("completion")
        self.assertIsInstance(
            completion, dict,
            f"completed job needs completion evidence: {result!r}",
        )
        self.assertEqual(
            completion.get("exported_clips"), 0,
            f"an unknown clip must not be counted as exported: {completion!r}",
        )
        self.assertTrue(
            result.get("warnings") or job.get("warnings") or result.get("error"),
            "a build that skipped the unknown clip must surface a warning",
        )


# ---------------------------------------------------------------------------
# Origin policy
# ---------------------------------------------------------------------------


class TestOriginPolicy(HangarTestCase):
    EVIL = "https://evil.example"

    def _assert_not_created(self, name):
        names = {str(p.get("name")) for p in self.state().get("presets", [])}
        self.assertNotIn(name, names,
                         "rejected cross-origin request must not persist a preset")

    def test_cross_origin_write_is_rejected(self):
        name = self._unique("cross-origin")
        reply = self.post(
            "/api/presets",
            {"name": name, "config": self._config()},
            headers={"Origin": self.EVIL},
        )
        self.assert_error(reply, (400, 403), "cross-origin POST")
        self._assert_not_created(name)

    def test_cross_origin_read_does_not_grant_cors(self):
        reply = self.get("/api/state", headers={"Origin": self.EVIL})
        self.assertNotIn(reply.status, (500,), reply.snippet())
        allow = (reply.header("access-control-allow-origin") or "").strip()
        self.assertNotIn(allow.lower(), ("*", self.EVIL.lower(), "null"),
                         f"cross-origin read must not be authorized: "
                         f"{reply.snippet()}")

    def test_preflight_does_not_authorize_evil_origin(self):
        reply = self.c.request(
            "OPTIONS", "/api/presets",
            headers={
                "Origin": self.EVIL,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "content-type",
            },
        )
        self.assertNotIn(reply.status, (500,), reply.snippet())
        allow = (reply.header("access-control-allow-origin") or "").strip()
        self.assertNotIn(allow.lower(), ("*", self.EVIL.lower(), "null"),
                         f"preflight must not authorize {self.EVIL}: "
                         f"{reply.snippet()}")

    def test_null_origin_write_is_rejected(self):
        name = self._unique("null-origin")
        reply = self.post(
            "/api/presets",
            {"name": name, "config": self._config()},
            headers={"Origin": "null"},
        )
        self.assert_error(reply, (400, 403), "null-origin POST")
        self._assert_not_created(name)

    def test_same_origin_write_is_allowed(self):
        name = self._unique("same-origin")
        reply = self.post(
            "/api/presets",
            {"name": name, "config": self._config()},
            headers={"Origin": self.service.origin},
        )
        self.assertIn(
            reply.status, (200, 201),
            f"same-origin studio writes must be allowed: {reply.snippet()}",
        )
        preset = reply.json()
        self.assertIn("id", preset, reply.snippet())
        self._preset_ids.append(preset.get("id"))
        matches = [p for p in self.state().get("presets", [])
                   if str(p.get("id")) == str(preset.get("id"))]
        self.assertEqual(len(matches), 1,
                         "same-origin preset should be visible in state")


# ---------------------------------------------------------------------------
# Static file isolation
# ---------------------------------------------------------------------------


class TestStaticIsolation(HangarTestCase):
    FORBIDDEN = {
        "/launch.py": ("def health(", "import ctypes"),
        "/START.cmd": ("launch.py",),
        "/data/launcher-server.json": ('"pid"',),
        "/docs/PLAN.md": ("H01 implementation",),
        "/tools/status.json": ('"task_ids"',),
        "/hangar/__init__.py": ("__version__",),
    }

    TRAVERSAL = (
        "/web/../launch.py",
        "/web/%2e%2e/launch.py",
        "/web/%2e%2e/%2e%2e/launch.py",
        "/%2e%2e/launch.py",
        "/web/..%2flaunch.py",
        "/web/..%5claunch.py",
        "/models/../../launch.py",
        "/models/%2e%2e/%2e%2e/launch.py",
        "/models/..%2f..%2flaunch.py",
        "/C:/Windows/win.ini",
    )

    def test_non_public_files_are_not_served(self):
        for path, needles in self.FORBIDDEN.items():
            with self.subTest(path=path):
                reply = self.get(path)
                self.assertNotEqual(
                    reply.status, 200,
                    f"non-public file must not be served: {reply.snippet()}",
                )
                self.assertNotIn(reply.status, (500,), reply.snippet())
                self.assert_no_disclosure(reply, needles, path)

    def test_encoded_traversal_cannot_read_source(self):
        needles = ("def health(", "import ctypes", "STATE = DATA")
        for path in self.TRAVERSAL:
            with self.subTest(path=path):
                reply = self.get(path)
                self.assertNotIn(reply.status, (500,),
                                 f"traversal must not fault: {reply.snippet()}")
                self.assert_no_disclosure(reply, needles, path)
                if path.endswith("win.ini"):
                    self.assert_no_disclosure(
                        reply, ("[fonts]", "[extensions]"), path)
                if reply.status == 200:
                    self.assertNotIn(
                        reply.content_type,
                        ("text/x-python", "application/x-python-code"),
                        f"traversal returned source code: {reply.snippet()}",
                    )

    def test_models_directory_has_no_listing_or_unknown_artifact(self):
        listing = self.get("/models/")
        self.assertIn(listing.status, (403, 404),
                      f"/models/ must not expose a listing: {listing.snippet()}")
        self.assert_no_disclosure(
            listing,
            ("launch.py", "server.log", "launcher-server.json", "index of"),
            "/models/",
        )

        unknown = self.get("/models/qa-unknown/model.glb")
        self.assertEqual(unknown.status, 404, unknown.snippet())

        nested = self.get("/models/qa-unknown/")
        self.assertIn(nested.status, (403, 404), nested.snippet())


# ---------------------------------------------------------------------------
# Export / dialog safety
# ---------------------------------------------------------------------------


class TestExportSafety(HangarTestCase):
    def test_export_requires_job_id(self):
        with tempfile.TemporaryDirectory(prefix="hangar-qa-export-") as folder:
            reply = self.post("/api/export", {"directory": folder})
        self.assert_error(reply, (400, 422), "export without job_id")

    def test_export_unknown_job_creates_no_artifacts(self):
        with tempfile.TemporaryDirectory(prefix="hangar-qa-export-") as folder:
            job_id = "qa-missing-" + uuid.uuid4().hex
            reply = self.post("/api/export",
                              {"job_id": job_id, "directory": folder})
            if reply.status in (400, 404, 409, 422):
                self.assert_error(reply, (400, 404, 409, 422),
                                  "export of unknown job")
            else:
                self.assertIn(
                    reply.status, (200, 202),
                    f"unexpected export status: {reply.snippet()}",
                )
                data = reply.json()
                export_job = data.get("job_id")
                self.assertTrue(export_job,
                                "accepted export must return job_id")
                self.assert_job_failed_without_result(
                    self.poll_job(export_job), "export of unknown job")

            artifacts = [p for p in Path(folder).iterdir()]
            self.assertEqual(
                artifacts, [],
                f"export without real evidence must create no files: {artifacts}",
            )

    def test_dialog_rejects_unknown_kind(self):
        reply = self.post("/api/dialog", {"kind": "qa-unknown-kind"})
        self.assert_error(reply, (400, 422), "unknown dialog kind")


# ---------------------------------------------------------------------------
# Preset CRUD (works in both managed and attached modes)
# ---------------------------------------------------------------------------


class TestPresets(HangarTestCase):
    def test_save_load_and_delete(self):
        name = self._unique("lifecycle")
        config = self._config()
        preset = self.create_preset(name=name, config=config)

        state = self.state()
        stored = self.find_preset(state, preset["id"])
        self.assertIsNotNone(stored, "saved preset must appear in /api/state")
        self.assertEqual(stored.get("name"), name)
        self.assertEqual(stored.get("config"), config,
                         "loaded preset must preserve its config")

        self.delete_preset(preset["id"])
        state = self.state()
        self.assertIsNone(self.find_preset(state, preset["id"]),
                          "deleted preset must disappear from /api/state")

    def test_rename_preserves_id_and_config(self):
        original = self._unique("rename")
        config = self._config()
        preset = self.create_preset(name=original, config=config)
        renamed = original + " renamed"

        reply = self.put(
            "/api/presets/" + urllib.parse.quote(str(preset["id"]), safe=""),
            {"name": renamed},
        )
        self.assertEqual(reply.status, 200, reply.snippet())
        updated = reply.json()
        self.assertEqual(updated.get("name"), renamed)

        stored = self.find_preset(self.state(), preset["id"])
        self.assertIsNotNone(stored, "renamed preset must keep its id")
        self.assertEqual(stored.get("name"), renamed)
        self.assertEqual(stored.get("config"), config,
                         "rename must not alter the config")

    def test_unknown_preset_ids_return_404(self):
        missing = "qa-missing-" + uuid.uuid4().hex
        path = "/api/presets/" + urllib.parse.quote(missing, safe="")
        self.assert_error(self.put(path, {"name": "x"}), (404,),
                          "rename unknown preset")
        self.assert_error(self.delete(path), (404,),
                          "delete unknown preset")

    def test_saved_presets_have_stable_ids(self):
        first = self.create_preset()
        second = self.state()
        again = self.state()
        self.assertIsNotNone(self.find_preset(second, first["id"]))
        self.assertIsNotNone(self.find_preset(again, first["id"]))
        ids = [str(p.get("id")) for p in again.get("presets", [])]
        self.assertEqual(len(ids), len(set(ids)),
                         "preset ids must be unique")


# ---------------------------------------------------------------------------
# Durable state across service recreation
# ---------------------------------------------------------------------------


class TestPresetDurability(HangarTestCase):
    @classmethod
    def setUpClass(cls):
        if SERVICE.external:
            raise unittest.SkipTest(
                "preset durability across service recreation requires the "
                "managed server; unset HANGAR_TEST_URL to run it"
            )

    def restart_and_client(self):
        self.service.restart()
        self.c = self.service.client()
        return self.c

    def test_save_survives_restart_rename_survives_restart_delete_survives_restart(self):
        name = self._unique("durable")
        config = self._config()
        preset = self.create_preset(name=name, config=config)
        preset_id = preset["id"]

        # 1. Save must be on disk before the process is recreated.
        self.restart_and_client()
        stored = self.find_preset(self.state(), preset_id)
        self.assertIsNotNone(
            stored,
            "preset must survive service recreation (durable state)",
        )
        self.assertEqual(stored.get("name"), name)
        self.assertEqual(stored.get("config"), config)

        # 2. Rename must also be durable.
        renamed = name + " renamed"
        path = "/api/presets/" + urllib.parse.quote(str(preset_id), safe="")
        reply = self.put(path, {"name": renamed})
        self.assertEqual(reply.status, 200, reply.snippet())

        self.restart_and_client()
        stored = self.find_preset(self.state(), preset_id)
        self.assertIsNotNone(stored, "renamed preset lost after recreation")
        self.assertEqual(stored.get("name"), renamed,
                         "rename was not durable")

        # 3. Deletion must be durable too.
        self.delete_preset(preset_id)
        self.restart_and_client()
        state = self.state()
        self.assertIsNone(self.find_preset(state, preset_id),
                          "deleted preset reappeared after recreation")
        names = {str(p.get("name")) for p in state.get("presets", [])}
        self.assertNotIn(renamed, names,
                         "deleted preset name reappeared after recreation")

    def test_deleted_preset_stays_deleted_after_restart(self):
        preset = self.create_preset()
        preset_id = preset["id"]
        self.delete_preset(preset_id)
        self.restart_and_client()
        self.assertIsNone(self.find_preset(self.state(), preset_id),
                          "deletion was not durable")


# ---------------------------------------------------------------------------


if __name__ == "__main__":
    unittest.main(verbosity=2)
