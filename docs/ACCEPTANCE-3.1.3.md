# ACBridge HANGAR 3.1.3 · 验收记录 / Acceptance record

范围：双语界面（i18n）与文档整理。不含新的 AC6 逆向能力；转换引擎与 3.1.2 相同。

## Automated regression

- Full `unittest` suite with the bundled runtime: **364 tests, 357 passed,
  3 skipped, 7 errors**. The 7 errors are pre-existing loader/fixture errors for
  research modules that were never part of the public tree
  (`probe_h11_motion`, `tools/prepare_h15_scoped_textures.py`, …); they fail
  identically on the untouched 3.1.2 baseline. No failure or error touches the
  i18n-changed paths (import, build, export, toolchain, capabilities, presets).
- Test-locked Chinese message substrings (`test_h04_scale.py`,
  `test_v312_toolchain.py`) still pass with verbatim zh templates.
- Static checks: all 52 `web/js/*.js` pass `node --check`; every `t()` /
  `data-i18n*` reference (785 keys) resolves in **both** languages against the
  real dictionaries (automated checker, 0 missing).
- Backend CJK audit: no user-facing Chinese string literals remain outside the
  per-module dictionaries; remaining literals are asset data
  (`NAMES`, identity defaults), comments and console diagnostics.
- `HANGAR.exe` rebuilt from `launcher/HangarLauncher.cpp` with the bilingual
  message table (MSVC `/W4 /WX`, build clean); version resources bumped to
  3.1.3.

## Real-browser i18n verification (Playwright + local Edge, isolated data dir)

Fresh profile, `HANGAR_DATA` pointed at a throwaway directory, real Edge via
Playwright. Evidence (screenshots + report.json):
`D:\Temp\hangar-i18n-gpmpb54i\` (en-main.png, zh-main.png,
en-first-run-wizard.png, en-wizard.png, zh-wizard.png).

| Check | Result |
|---|---|
| First launch auto-detects English from a `en-US` browser locale | PASS (`Import Wizard` rendered, `<html lang="en">`) |
| Fresh-profile first-run wizard auto-opens, in English | PASS (title `Local resource setup and import wizard`) |
| Header 中 button switches the whole UI immediately, no reload | PASS (nav, catalog, HUD, right-panel feature modules all zh) |
| `<html lang>` follows the active language | PASS |
| Choice persists in `localStorage('hangar_ui_lang')` and across reload | PASS (`zh-CN` restored after reload) |
| Switch back to EN and open the wizard in both languages | PASS (modal title + `Install missing components` verified in en and zh) |
| Header layout does not overflow in either language (scrollWidth check) | PASS (en and zh, 1600×1000) |
| Page JS errors during the whole flow | PASS (0 errors) |
| `GET /api/state` honors `X-Hangar-Lang` | PASS (Game-Root reason returned in en and zh) |
| Error localization by request language (`POST /api/import` invalid roots) | PASS (`Select at least one expanded resource directory.` / `请选择至少一个已展开资源目录。`) |
| Closing the window stops the service started for this verification | PASS (`stop.py` exit 0) |

Two defects were found by this verification and fixed before packaging:

1. The right-panel feature-module block kept its initial language after a
   switch (shell titles and status badges were JS-generated without refresh
   hooks). Fixed via `data-i18n` in the shell, a status-badge refresh in
   `updatePlayerFeatures`, and an `updatePlayerFeatures` call in
   `refreshForLanguage`.
2. `web/vendor/three` was a junction in the development tree, so the loopback
   server correctly refused to serve it (404 → the app could not boot in a
   browser from the dev tree). Replaced with a real directory copy
   (git-ignored); packaging is unaffected because it copies real files.

## Portable package acceptance (r16, verify_h24_package)

The exact release ZIP was verified end to end: every member hash matches
`PACKAGE-MANIFEST.json`, the ZIP round-trips (archive vs extracted hashes), the
bundled runtime resolves numpy/scipy/PIL/acbridge inside its own prefix, and
shipped files stay byte-identical after the runs.

| Check | Result |
|---|---|
| First run on a brand-new empty profile | PASS (service ready 2.8 s, library genuinely empty, clean stop exit 0) |
| Populated real-resource profile | PASS (ready 2.0 s; library 268 assets: player+enemy+weapon) |
| Real player build with a000_020100 walk | PASS (GLB with skinned mesh, 2 exported animation tracks) |
| Real standalone C3000 preview + export | PASS (glTF validator 0 errors; export hash equals artifact hash) |
| Invalid import (empty directory) | PASS (job fails honestly; previous catalog and model preserved; preview cache still hits) |
| Runtime isolation | PASS (no user-site packages; all modules resolve inside `runtime/`) |

Evidence: `D:\Temp\hangar-v313-verify\verify-report.json` (local).

## Honest boundaries

- zh-to-en coverage covers every surface reachable in normal builds. The
  experimental FXR research views and the two unlinked research dashboards
  remain Chinese-only (documented in KNOWN-LIMITATIONS.md).
- Stored job history keeps the language it was produced in; already-shown
  toasts are not retro-translated on switch.
- Long-duration GPU / photography sessions and manual visual appearance checks
  are unchanged from 3.1.2 and remain open (`gui_acceptance=false` carries
  over for those aspects; the i18n flows above were verified in a real
  browser).
