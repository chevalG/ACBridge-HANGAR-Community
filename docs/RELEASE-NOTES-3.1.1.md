# ACBridge HANGAR 3.1.1

HANGAR 3.1.1 is the first public Windows x64 release of the H24 line.

## Included

- Direct read-only initialization from a legally installed AC6 Game Root.
- Automatic Game Root import for player parts, player motion, enemy models and
  enemy motion, and weapons.
- Player four-slot assembly, including cores without a part-local compendium.
- Player, enemy/boss and weapon library synchronization after import.
- Native HKX playback for compatible source skeleton/clip pairs.
- Collection identities, material inspection, photography and GLB export.
- Repeated library snapshot cache with content-aware invalidation.
- Experimental FXR controls hidden by default.

## Public package changes

- Replaced the stale WitchyBND URL with the official pinned release
  `3.0.1.0` and SHA-256 verification.
- Added explicit Microsoft .NET 10 Desktop Runtime readiness checks.
- Corrected third-party notices and corresponding-source references.
- Removed statements from packaged notices that incorrectly described bundled
  open-source components as externally unavailable.
- Retained WitchyBND as a user-obtained tool because its upstream Windows
  release contains a proprietary Oodle component.

## Verification boundary

The exact candidate archive passed machine-level extraction, hashes, startup,
API, first-run import, player build/rebuild, enemy motion conversion, weapon
preview, animated export, recovery and restart-persistence checks. Final manual
GUI appearance, playback/seek inspection and longer GPU sessions remain
documented as unverified.
