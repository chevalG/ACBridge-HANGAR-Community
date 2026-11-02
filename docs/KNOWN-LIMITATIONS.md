# Known limitations

- Since 3.1.3 the interface is bilingual (English / Simplified Chinese), but
  the experimental FXR research views (`fxr-*.js`, `source-*.js`) and the two
  unlinked research dashboards (`h12-coverage.html`, `h23-motion-coverage.html`)
  remain Chinese-only. They are hidden behind the experimental feature flag and
  are not part of the supported public workflow.
- Stored job history keeps the language it was produced in; switching the
  interface language does not retroactively translate old messages. New jobs
  follow the current language.
- Normal builds hide unfinished FXR and laser-blade runtime controls. The
  experimental research code remains available only through an internal
  environment switch and is not part of the supported public workflow.
- Material and Shader reconstruction is partial. Some surfaces use
  source-backed approximations and will not match the game pixel for pixel.
- A source clip may exist while its skeleton, container stage or additive setup
  is incompatible. HANGAR reports these states separately and does not invent a
  binding.
- Free weapon mounting remains experimental. Weapon-bay folding, the complete
  shoulder animation, IK and combat behavior are not reproduced.
- GLB export contains the model, supported materials and native motion. It does
  not include the experimental FXR runtime.
- The final 3.1.2 package has machine-level end-to-end checks but
  `gui_acceptance=false`: manual visual appearance, playback/seek appearance and
  long-duration GPU sessions were not completed.
- The desktop shell requires Microsoft Edge WebView2 Runtime. Current Windows
  10/11 installations normally have it; otherwise the launcher shows the
  official runtime link instead of opening a broken window.
- AC6 part bundles use KRAK compression. HANGAR needs the user's own
  `oo2core_8_win64.dll` beside WitchyBND; it is not downloaded or redistributed.
  Without it, Game Root can index animations but not player parts.
- Closing the HANGAR window requests a graceful stop. A wedged third-party
  unpacker can delay that stop until its timeout; the launcher then terminates
  only its own child process tree.
- The desktop icon is derived from game emblem artwork supplied by the project
  owner. See `THIRD-PARTY-NOTICES.md`; no ownership of the original artwork is
  claimed.
- The complete regression run retained 2 failures requiring an absent default
  C4201 body/head layout and 9 setup errors requiring a missing historical H06
  frozen fixture. The two C4201 tests pass with the genuine source layout. No
  failing test was hidden or replaced with fabricated data.
- Game Root extraction depends on the user's AC6 version and local disk speed.
  WitchyBND and .NET 10 Desktop Runtime must be available before the Game Root
  option is enabled.
