# ACBridge HANGAR 3.1.2

HANGAR 3.1.2 is a desktop-experience update on the stable 3.1.1 baseline. It
does not add AC6 reverse-engineering features.

## Desktop entry

- `HANGAR.exe` is a native Windows shell with a proper HANGAR icon, taskbar
  identity and a WebView2 application window.
- Double-clicking starts the local service, waits for its own state record and
  opens the HANGAR interface. No command prompt is shown.
- A single-instance mutex re-activates the existing window rather than starting
  a second service.
- `START.cmd`, `STOP.cmd` and the VBS launcher remain available for debugging.

## Lifecycle

- Closing the HANGAR window stops the exact service process started by that
  window, including its child processes, then exits the desktop shell.
- The launcher does not enumerate or terminate unrelated Python processes.
- Shutdown is graceful: queued jobs are cancelled, running work is asked to
  stop, and the job pool is waited on before exit. A job-object fallback only
  applies to the launcher's own child tree.
- Restart, repeated launch and occupied-port handling are covered by the
  service's existing port allocation and state-file logic.

## Dependencies

- The import wizard shows NuxeBridge, Nuxe index, .NET 10 Desktop Runtime and
  WitchyBND status.
- **一键安装缺失组件** reuses `tools/install_game_tools.py`, the reviewed 3.1.1
  assistant, instead of a parallel downloader.
- The assistant reuses existing components, downloads WitchyBND only from its
  pinned official GitHub release and verifies SHA-256 before installation.
- Manual WitchyBND selection and Expanded mode remain available. Missing or
  failed dependencies do not block already-usable Expanded resources.
- AC6 part bundles are KRAK-compressed. HANGAR does not redistribute the
  proprietary Oodle DLL; the assistant can copy `oo2core_8_win64.dll` from the
  user's own AC6 installation. Without it, Game Root indexes animations but
  cannot unpack player parts.
- A failed or offline install reports the failure, writes a log, and leaves the
  UI ready for retry.

## Compatibility

- The 3.1.1 Game Root / Expanded import, player assembly, enemy collection,
  native animation, materials, photography and GLB export paths are unchanged
  except for version reporting and the dependency status surface.
- Existing profiles under `%LOCALAPPDATA%\ACBridge\HANGAR\data` are reused.
- 3.1.1 remains available for rollback.
