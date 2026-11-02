# Release acceptance record

## Confirmed by machine tests

- Exact archive extraction and per-file SHA-256 verification.
- Fresh portable data directory, bundled runtime and loopback launch.
- Game Root extraction from the local legally installed AC6 build.
- 100 player parts, 166 enemy entries and 33 weapon entries in the first-run
  workflow profile.
- First player build and a second build using `BD_M_3310`.
- C4200: 169 native clips; C6100: 83 native clips.
- Supported weapon preview and animated player GLB export.
- Invalid-input recovery without deleting the previous valid catalog.
- Restart persistence and package-file integrity after workflows.

## Not claimed

- Human GUI acceptance is `false`.
- Photography appearance, visual playback/seek quality and long GPU stability
  remain manual checks.
- The complete test suite retained 2 default-layout C4201 failures and 9 setup
  errors from a missing historical H06 fixture. The two C4201 tests pass when
  the genuine source layout is available. Nineteen conditional tests were
  skipped.

The release is published with these limitations disclosed so users can evaluate
the candidate honestly. Machine success is not described as visual parity.
