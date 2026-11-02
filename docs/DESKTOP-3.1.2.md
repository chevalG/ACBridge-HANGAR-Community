# Desktop shell and lifecycle

## Why a native shell

The 3.1.1 entry point launched a hidden Python process and an ordinary browser
window. That left two gaps: a browser tab could not reliably signal "HANGAR
closed", and an intermediate `launch.py` process made ownership ambiguous.

3.1.2 uses a small C++ Win32 executable (`HANGAR.exe`) with Microsoft's
WebView2 control. The executable is about 250 KB and links the WebView2 loader
statically. It does not bundle a browser engine: WebView2 uses the Microsoft
Edge WebView2 Runtime already installed by Windows or Edge.

## Process ownership

- The launcher creates the actual `python -m hangar` process with
  `CreateProcessW` and keeps its process handle.
- It also places that child in a job object. The job is a fallback boundary,
  not the primary shutdown mechanism.
- The service writes `launcher-server.json` after binding loopback; the
  launcher waits for that record rather than scraping an unrelated port.
- On `WM_CLOSE`, the launcher closes WebView2, runs the bundled `stop.py` for
  the same data directory, waits for the owned process, then closes the job.
- It never calls `taskkill` by process name and never searches for generic
  Python processes.

## Single instance

A named local mutex prevents a second shell from starting a second service.
The second launch signals the first window to restore and focus. A stale
mutex disappears with the owning process; a stale service is detected by the
state file and health check.

## Shutdown semantics

`App.close(graceful=True)` is used by the normal service exit path. It marks
the service as closing, cancels queued jobs, asks running jobs to stop, and
waits for the job pool. The existing atomic JSON writers and per-job artifact
files remain the integrity mechanism. If a process is wedged beyond the
shutdown timeout, the launcher terminates only the job tree it owns.

## Developer entry points

- `START.cmd` and `HANGAR.vbs` remain for debugging and for environments without
  WebView2.
- `STOP.cmd` still stops the instance recorded for the current data directory.
- `HANGAR.exe --health-check` starts and stops an owned service without showing
  a window; it is used by packaging checks.
