# ACBridge HANGAR

[English](README.md) | **简体中文**

一个完全本地的 Windows 机甲整备工作室：面向你合法安装的《装甲核心 VI：境界天火》（*ARMORED CORE VI FIRES OF RUBICON*），浏览与检视机体、部件与武器。HANGAR 支持玩家四槽装配、玩家/敌方/武器馆藏索引、已支持的原版动作播放、材质检视、摄影，以及静态或动态 GLB 导出。

所有功能都在本机运行：HANGAR 只读读取你自己的游戏安装，从不上传、再分发或修改任何游戏数据。

- 当前稳定版本：**[v3.1.3](https://github.com/chevalG/ACBridge-HANGAR-Community/releases/latest)**
- 界面提供**简体中文与英文**双语：首次启动按 Windows 显示语言自动选择，之后随时用右上角的 中 / EN 按钮切换，选择会被记住。

## 适合谁用

- 想在游戏外研究自己机体、敌方与武器的 AC6 玩家；哪些内容已验证、哪些只是近似，界面都会如实标注。
- 需要干净 GLB 导出（静态或动画）的创作者，可用于渲染、动画或参考。
- 喜欢便携工具的人：解压、双击 `HANGAR.exe` 即可，无需安装器，关闭窗口后不留后台进程。

## 下载与首次运行

1. 从 [Releases 页面](https://github.com/chevalG/ACBridge-HANGAR-Community/releases/latest)下载当前的
   `ACBridge-HANGAR-H24-r16-Windows-x64.zip`，**完整解压**到一个新目录（本地磁盘，不要放网络盘）。
2. 双击 `HANGAR.exe`，不需要命令行。
3. 用顶栏的中 / EN 按钮选择界面语言，选择会跨会话保留。
4. 打开顶栏的**资源导入向导**：
   - **Expanded（展开资源）**模式始终可用，不需要任何额外组件。把它指向一个已展开的资源目录（包含 `parts/`、`clips/`、`Skeleton.hkx`、`.compendium`），HANGAR 会在原位建立索引。
   - **Game Root（游戏原版目录）**模式直接读取 AC6 安装目录。一键安装会复用包内 Nuxe 组件，仅在你主动点击时才从上游官方发布页获取 WitchyBND 并校验固定 SHA-256，同时复查 .NET 10 Desktop Runtime。AC6 的部件包使用 KRAK 压缩，请同时选择**从 AC6 目录复用 Oodle**，让 HANGAR 从你自己的游戏安装中复制 `oo2core_8_win64.dll`。
5. 在左侧四槽目录装配机体，在右侧动作库挑选动作，点击**构建**。用完直接关窗；该窗口启动的服务会随之停止。

首次 Game Root 导入会生成 HANGAR 自己的缓存。你的游戏安装、原版分卷与已有资源不会被修改。

## 包内包含什么

- Windows x64 便携运行时与查看器（Python、Node、Three.js 与 glTF 校验器均已内置）。
- 兼容 GPL 的 Nuxe 1.2.1 组件与 ACBridge 的 NuxeBridge 适配层。
- 带有 NIGHTFALL HANGAR 图标与 WebView2 窗口的原生 Windows 桌面壳。
- Apache-2.0 授权的 BinderKeys AC6 索引数据。
- 许可证文本、对应源码位置与重建信息。
- 不包含任何 FromSoftware 模型、贴图、动画、FXR、游戏分卷、regulation 数据、导入资源、用户配置、截图、导出 GLB 或研究捕获。

WitchyBND 不随包分发。其官方 Windows 版本包含专有的 Oodle 组件，因此 HANGAR 只在你运行安装助手时，才从上游作者处直接获取该版本。见
[THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md)。

HANGAR 不会下载或再分发 `oo2core_8_win64.dll`。如果你的 AC6 安装里含有它，安装助手可以从你自己的游戏目录复制到 WitchyBND 旁。该运行时是解包 KRAK 压缩部件包所必需的。

## 主要能力

- 基于真实来源骨架、动画与材质处理的四槽机体装配。
- 从所选游戏目录或展开资源建立的玩家、敌机/BOSS 与武器可搜索馆藏。
- 源骨架与动作兼容时的原生 HKX 播放，支持暂停、跳转、倍速与循环。
- 按馆藏编辑名称、别名与备注；身份记录同时保留原始 ID 与登记名称。
- 透视视角、线框、骨骼辅助显示，以及带灯光预设与高清 PNG 输出的摄影室。
- 返回真实任务与结果路径的静态 / 动画 GLB 导出。

## 合法游戏资源

HANGAR 要求一份合法获得的《装甲核心 VI》。它不包含、不下载、也不绕过任何内容，只读读取你自己副本中的文件。如果你使用展开资源，它们必须来自你自己的合法游戏副本。

## 已知限制

- 见[已知限制](docs/KNOWN-LIMITATIONS.md)、[3.1.3 验收记录](docs/ACCEPTANCE-3.1.3.md)与[性能摘要](docs/PERFORMANCE-3.1.2.md)。
- Shader 与材质重建仍是部分的；恢复的外观不是对游戏画面的逐像素复刻。
- 部分展开、武器、动作与 Additive 组合仍不兼容、未测试或标记为实验性。
- 自由武器挂载是实验性的；游戏的完整肩部折叠、IK 与战斗行为未复现。
- FXR 粒子播放不包含在导出的 GLB 中；实验性 FXR 研究视图在常规构建中隐藏。

## 许可与独立性

HANGAR 应用代码采用 GPL-3.0-only。第三方组件保留其各自的许可与署名，见
[NOTICE.md](NOTICE.md) 与 [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md)。

这是一个非官方社区项目，与 FromSoftware、Bandai Namco 或《装甲核心 VI》的发行方没有任何隶属、背书或赞助关系。ACBridge HANGAR 不分发游戏或游戏资产，用户必须使用自己合法获得的游戏安装。

## 从源码构建

克隆本仓库后参阅 [docs/SOURCE-BUILD.md](docs/SOURCE-BUILD.md) 了解工具链、运行时清单与打包脚本。发布包由显式白名单脚本组装，拒绝包含游戏资产或用户数据。
