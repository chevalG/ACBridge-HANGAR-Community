# ACBridge HANGAR 3.1.2

ACBridge HANGAR is a local Windows studio for browsing and inspecting mechs,
parts and weapons from a legally installed copy of *ARMORED CORE VI FIRES OF
RUBICON*. It can assemble a player AC from four parts, index player and enemy
collections, play supported native motion clips, inspect recovered materials,
take photographs and export static or animated GLB files.

ACBridge HANGAR 是一个面向《装甲核心 VI：境界天火》的本地机甲浏览与整备工作室。
它可以从玩家合法安装的游戏中只读提取资源，支持玩家四槽装配、玩家/敌方/武器馆藏、
已支持的原版动作、材质浏览、摄影和静态或动态 GLB 导出。

## Download and first run / 下载与首次运行

1. Download and fully extract `ACBridge-HANGAR-H24-r11-Windows-x64.zip`.
2. Double-click `HANGAR.exe`. No command prompt is required.
3. If the import wizard reports missing game tools, use **一键安装缺失组件**.
   It reuses the bundled Nuxe stack, obtains WitchyBND from its official GitHub
   release, verifies the pinned SHA-256, and rechecks .NET 10 Desktop Runtime.
   For AC6's KRAK-compressed part bundles, also select **从 AC6 目录复用 Oodle**
   so HANGAR copies `oo2core_8_win64.dll` from your own game installation.
4. Choose **Game Root** and select either the AC6 installation folder or its
   `Game` subfolder. **Expanded** remains available without those tools.
5. Closing the HANGAR window stops the service and child processes started by
   that window.

已展开资源仍可直接使用 **Expanded** 模式，无需再次解包。Game Root 首次导入会生成
HANGAR 自己的缓存；不会修改游戏安装、原版分卷或用户已有资源。

## What is included / 包内内容

- Windows x64 portable HANGAR runtime and viewer.
- GPL-compatible Nuxe 1.2.1 components and ACBridge's NuxeBridge adapter.
- A native Windows desktop shell with the NIGHTFALL HANGAR icon and WebView2
  window.
- Apache-2.0 BinderKeys AC6 index data.
- License texts, corresponding-source locations and rebuild information.
- No FromSoftware models, textures, animations, FXR, game archives, regulation
  data, imported resources, user profiles, screenshots, exported GLBs or
  research captures.

WitchyBND is not redistributed. Its official Windows release contains a
proprietary Oodle component, so HANGAR retrieves that release directly from the
upstream author only when the user runs the setup assistant. See
[THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md).

HANGAR does not download or redistribute `oo2core_8_win64.dll`. If your AC6
installation contains it, the setup assistant can copy it from your own game
directory beside WitchyBND. That runtime is required to unpack KRAK-compressed
part bundles.

## Main capabilities / 主要能力

- Four-slot player AC assembly with source-backed skeletons, animation and
  material handling.
- Searchable player, enemy/boss and weapon collections populated from the
  selected game directory.
- Native HKX playback, pause, seek and speed controls where the source skeleton
  and clip are compatible.
- Collection-aware editing of names, aliases and notes.
- Perspective views, wireframe, skeleton overlays and photography.
- Static and animated GLB export with real job/result paths.

## Known limits / 已知限制

- See [known limitations](docs/KNOWN-LIMITATIONS.md), the
  [acceptance record](docs/ACCEPTANCE-3.1.2.md), and the
  [performance summary](docs/PERFORMANCE-3.1.2.md).
- GPL FXR and laser-blade experiments are hidden in normal builds. The GLB does
  not contain the experimental FXR runtime.
- Shader and material reconstruction remains partial; recovered appearance is
  not a pixel-identical recreation of the game.
- Some expansion, weapon, clip and Additive combinations remain incompatible,
  untested or marked experimental.
- Free weapon mounting is experimental. The game's complete shoulder-folding,
  IK and combat behavior are not reproduced.
- Machine package checks passed for the release candidate, but final manual GUI,
  long-session GPU and visual acceptance remain incomplete. This limitation is
  recorded rather than presented as a pass.
- Full regression retained 2 failures that require an absent default C4201
  layout and 9 setup errors that require a missing historical H06 fixture.
  Both C4201 tests pass with the genuine source layout; no test was hidden or
  given a fabricated fixture.

## License and independence / 许可与独立性

HANGAR application code is GPL-3.0-only. Third-party components retain their own
licenses and attributions. See [NOTICE.md](NOTICE.md) and
[THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md).

This is an unofficial community project. It is not affiliated with, endorsed
by, or sponsored by FromSoftware, Bandai Namco or the publishers of ARMORED
CORE VI. ACBridge HANGAR does not distribute the game or game assets. Users
must supply their own legally obtained installation.
