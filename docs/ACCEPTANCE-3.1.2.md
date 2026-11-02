# Release acceptance record — 3.1.2

Baseline: public `v3.1.1` source (`6688599`), packaged with the audited Nuxe
inputs from the published 3.1.1 build. No private research history was used.

## Confirmed by machine tests

- Exact archive extraction and per-file SHA-256 verification.
- Clean extracted package: `HANGAR.exe` starts the owned loopback service, opens
  a WebView2 window, and reports `version=3.1.2`, engine `0.5.1a5+h03.1`.
- First run created a brand-new data directory without manual setup.
- Dependency status reported NuxeBridge and Nuxe index present, .NET 10 Desktop
  Runtime present, WitchyBND missing, Oodle missing, Expanded still available.
- One-click install downloaded WitchyBND 3.0.1.0 from the pinned official
  release, verified SHA-256, installed it, and re-detected all components.
  A transient SSL EOF on the first attempt was reported as a retryable failure;
  the second attempt completed.
- The "从 AC6 目录复用 Oodle" path copied the user's own
  `oo2core_8_win64.dll` beside WitchyBND; HANGAR did not download or bundle it.
- Full Game Root import then produced 100 parts (96 buildable), 121 native
  clips and 186 weapons.
- Player assembly completed with one native animation; enemy `C3000` preview
  and weapon `WP_A_3010` preview passed the GLB validator with 0 errors.
- Exported player GLB SHA-256 matched the built artifact.
- A second install request completed as "already ready" without re-downloading.
- Closing the window stopped HANGAR.exe, its owned Python service and the
  WebView2 process tree; no state marker was left behind.
- Second launch exited immediately and re-activated the existing window; the
  service port stayed unchanged.
- Existing `settings.json` was preserved across restart.

## Not claimed

- Human GUI visual acceptance is still separate from these machine checks.
- Photography appearance, playback/seek appearance and long-duration GPU
  sessions were not re-certified in this small desktop update.
- The full suite retains the 3.1.1 baseline's environment-dependent errors
  (missing historical fixtures and absent `acbridge` import path in a bare
  source checkout). New 3.1.2 tests pass; no failing test was hidden.
- No new AC6 reverse-engineering behavior was added or certified.
