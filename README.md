# ACBridge HANGAR

**English** | [简体中文](README.zh-CN.md)

A local Windows studio for browsing and inspecting mechs, parts and weapons
from a legally installed copy of *ARMORED CORE VI FIRES OF RUBICON*. HANGAR
assembles a player AC from four parts, indexes player, enemy and weapon
collections, plays supported native motion clips, inspects recovered
materials, takes photographs, and exports static or animated GLB files.

Everything runs on your machine. HANGAR reads your own game installation
read-only and never uploads, redistributes or modifies game data.

- Current stable release: **[v3.1.3](https://github.com/chevalG/ACBridge-HANGAR-Community/releases/latest)**
- The interface ships in **English and Simplified Chinese**. HANGAR picks the
  Windows display language on first start; switch anytime with the
  中 / EN buttons in the top-right corner.

## Who it is for

- AC6 players who want to study their own AC, enemies and weapons outside the
  game, with honest labels for what is verified and what is approximate.
- Creators who need clean GLB exports (static or animated) for renders,
  animations or reference work.
- People who prefer a portable tool: unzip, double-click `HANGAR.exe`, no
  installer and no background services left behind after closing.

## Download and first run

1. Download the current `ACBridge-HANGAR-H24-r16-Windows-x64.zip` from the
   [Releases page](https://github.com/chevalG/ACBridge-HANGAR-Community/releases/latest)
   and extract it **fully** into a new folder (a local disk, not a network
   drive).
2. Double-click `HANGAR.exe`. No command prompt is required.
3. Pick the interface language with the 中 / EN buttons in the header. The
   choice is remembered across sessions.
4. Open the **Import Wizard** from the header:
   - **Expanded** mode is always available and needs no extra components. Point
     it at a directory of expanded game resources (`parts/`, `clips/`,
     `Skeleton.hkx`, `.compendium`) and HANGAR indexes it in place.
   - **Game Root** mode reads the AC6 installation directly. Its one-click
     setup reuses the bundled Nuxe stack, fetches WitchyBND from the official
     upstream release only when you ask for it, verifies the pinned SHA-256,
     and rechecks .NET 10 Desktop Runtime. For AC6's KRAK-compressed part
     bundles, also choose **reuse Oodle from the AC6 directory** so HANGAR can
     copy `oo2core_8_win64.dll` from your own game installation.
5. Assemble a mech from the four-slot catalog on the left, pick motions from
   the library on the right, and press **Build**. Close the window when done;
   the services started by that window stop with it.

First Game Root import builds HANGAR's own cache. Your game installation,
original archives and existing resources are never modified.

## What is in the package

- Windows x64 portable HANGAR runtime and viewer (Python, Node, Three.js and
  the glTF validator are bundled).
- GPL-compatible Nuxe 1.2.1 components and ACBridge's NuxeBridge adapter.
- A native Windows desktop shell with the NIGHTFALL HANGAR icon and a WebView2
  window.
- Apache-2.0 BinderKeys AC6 index data.
- License texts, corresponding-source locations and rebuild information.
- No FromSoftware models, textures, animations, FXR, game archives, regulation
  data, imported resources, user profiles, screenshots, exported GLBs or
  research captures.

WitchyBND is not redistributed. Its official Windows release contains a
proprietary Oodle component, so HANGAR retrieves that release directly from
the upstream author only when you run the setup assistant. See
[THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md).

HANGAR does not download or redistribute `oo2core_8_win64.dll`. If your AC6
installation contains it, the setup assistant can copy it from your own game
directory beside WitchyBND. That runtime is required to unpack KRAK-compressed
part bundles.

## Main capabilities

- Four-slot player AC assembly with source-backed skeletons, animation and
  material handling.
- Searchable player, enemy/boss and weapon collections populated from the
  selected game directory or expanded resources.
- Native HKX playback with pause, seek, speed and loop controls where the
  source skeleton and clip are compatible.
- Collection-aware editing of names, aliases and notes; identity records keep
  both the original IDs and the names recorded for them.
- Perspective views, wireframe, skeleton overlays, a photo studio with lighting
  presets and HD PNG capture.
- Static and animated GLB export with real job and result paths.

## Legal game resources

HANGAR requires a legally obtained installation of ARMORED CORE VI. It does
not include, download, or bypass anything; it reads files from your own copy
read-only. If you use expanded resources, they must come from your own legally
obtained game.

## Known limits

- See [known limitations](docs/KNOWN-LIMITATIONS.md), the
  [3.1.3 acceptance record](docs/ACCEPTANCE-3.1.3.md) and the
  [performance summary](docs/PERFORMANCE-3.1.2.md).
- Shader and material reconstruction remains partial; recovered appearance is
  not a pixel-identical recreation of the game.
- Some expansion, weapon, clip and Additive combinations remain incompatible,
  untested or marked experimental.
- Free weapon mounting is experimental. The game's complete shoulder-folding,
  IK and combat behavior are not reproduced.
- FXR particle playback is not part of the exported GLB; experimental FXR
  research views are hidden in normal builds.

## License and independence

HANGAR application code is GPL-3.0-only. Third-party components retain their
own licenses and attributions. See [NOTICE.md](NOTICE.md) and
[THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md).

This is an unofficial community project. It is not affiliated with, endorsed
by, or sponsored by FromSoftware, Bandai Namco or the publishers of ARMORED
CORE VI. ACBridge HANGAR does not distribute the game or game assets. Users
must supply their own legally obtained installation.

## Building from source

Clone this repository and see [docs/SOURCE-BUILD.md](docs/SOURCE-BUILD.md) for
the toolchain, the runtime manifest and the packaging scripts. Release
packages are assembled by explicit whitelisted scripts that refuse to include
game assets or user data.
