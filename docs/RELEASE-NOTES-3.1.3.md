# ACBridge HANGAR 3.1.3 / 发行说明

**English** · [简体中文](#简体中文)

HANGAR 3.1.3 is a documentation and internationalization update on the stable
3.1.2 baseline. It adds no AC6 reverse-engineering features and does not change
the conversion engine.

## English

### Bilingual interface (English / 简体中文)

- The whole desktop interface ships in English and Simplified Chinese: header
  navigation, four-slot assembly, parts catalog, museum and search, the import
  wizard, Game Root / Expanded import, missing-dependency detection and
  one-click setup, the motion player and timeline, the photo studio, exports,
  identity and notes editing, status labels, loading hints, notifications,
  error banners and confirmation dialogs.
- First start picks the Windows display language (via the browser locale) and
  falls back to English when it cannot be identified. The 中 / EN buttons in
  the header switch immediately, and the choice persists across sessions.
- Switching languages never rebuilds the resource index, rewrites user data or
  restarts the background service. The currently loaded mech, collection,
  motions, materials and export targets stay untouched.

### Backend messages

- Import, assembly, export, dependency setup and job progress messages follow
  the interface language of the requesting client.
- Raw diagnostics from third-party tools and the frozen engine keep their
  technical detail; the UI adds a localized summary for known error codes and
  always keeps the original message available in the technical diagnostics
  section.
- Asset data is never translated: part IDs, motion IDs, file paths, coverage
  evidence and user-edited names or notes stay exactly as recorded. Collection
  identity names are shown per language when a record carries both
  (`names.zh` / `names.en`); no record is rewritten.
- The `HANGAR.exe` desktop shell follows the Windows display language for its
  startup and error dialogs.

### Documentation

- The README is now split into a pure English `README.md` and a pure
  Simplified Chinese `README.zh-CN.md`, each linking to the other.
- The outdated `r12` package reference in the previous README was replaced by
  the stable release entry, and the docs now describe the bilingual interface,
  legal-game-resource requirements, external tool setup and known limits in
  both languages.

## 简体中文

### 双语界面（English / 简体中文）

- 整个桌面界面提供英文与简体中文双语：顶栏导航、四槽装配、零件目录、馆藏与
  搜索、资源导入向导、Game Root / Expanded 导入、缺失依赖检测与一键安装、动
  作播放器与时间轴、摄影室、导出、身份与备注编辑、状态标签、加载提示、通知、
  错误横幅与确认对话框。
- 首次启动按 Windows 显示语言（经浏览器区域语言）自动选择，无法识别时回退英
  文。顶栏的中 / EN 按钮即时切换，选择跨会话保留。
- 切换语言不会重建资源索引、不改写用户数据、不重启后台服务；当前机体、馆藏、
  动作、材质与导出目标保持不变。

### 后端消息

- 资源导入、装配、导出、依赖安装与作业进度消息按请求端的界面语言返回。
- 来自第三方工具与冻结引擎的原始诊断保留技术详情；界面对已知错误码给出本地
  化摘要，原始消息始终可在「技术诊断」区查看。
- 资产数据不做翻译：部件 ID、动作 ID、文件路径、覆盖证据与用户编辑的名称备
  注保持原样；馆藏身份名称在记录同时含 `names.zh` / `names.en` 时按语言展示，
  不改写任何记录。
- `HANGAR.exe` 桌面壳的启动与错误对话框跟随 Windows 显示语言。

### 文档

- README 拆分为纯英文 `README.md` 与纯简体中文 `README.zh-CN.md`，顶部互相链
  接。
- 旧版 README 中过期的 `r12` 包名改为稳定 Release 入口；文档以中英双语说明双
  语界面、合法游戏资源要求、外部工具安装与已知限制。

## Compatibility / 兼容性

- The 3.1.2 Game Root / Expanded import, player assembly, enemy collection,
  native animation, materials, photography and GLB export paths are unchanged
  except for message wording. 3.1.2 / 3.1.2(r15) remains available for
  rollback.
- Existing profiles under `%LOCALAPPDATA%\ACBridge\HANGAR\data` are reused;
  Chinese-language users keep their current experience (zh remains the
  fallback for unrecognized locales).
- The bundled engine, runtime and dependency toolchain are identical to 3.1.2.
- Experimental FXR research views (hidden unless the experimental feature flag
  is enabled) and the unlinked research dashboards remain Chinese-only; see
  [KNOWN-LIMITATIONS.md](KNOWN-LIMITATIONS.md).
