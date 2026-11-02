# Installation / 安装

## English

### Requirements

- 64-bit Windows 10 or Windows 11.
- A legally installed PC copy of *ARMORED CORE VI FIRES OF RUBICON*.
- Internet access for the first setup if WitchyBND or the .NET 10 Desktop
  Runtime is not already installed.

### First setup

1. Extract the entire ZIP to a normal writable folder. Do not run it from
   inside the ZIP.
2. Double-click `HANGAR.exe`. The app starts its local service and opens its
   own window.
3. In the import wizard, use **一键安装缺失组件** if the toolchain is missing.
   The assistant reuses installed components, downloads WitchyBND only from its
   official GitHub release, verifies the pinned SHA-256, and rechecks .NET.
4. If **Microsoft .NET 10 Desktop Runtime (x64)** is still missing, install it
   from <https://dotnet.microsoft.com/download/dotnet/10.0>, then retry.
5. Choose **Game Root** and select the AC6 installation folder or its `Game`
   subfolder. **Expanded** works without the unpacking tools.
6. Wait for extraction and indexing. Source game files remain read-only.
7. Close the HANGAR window normally. The service started by that window stops.

You can also choose **Expanded** and select an existing unpacked tree containing
player parts, clips, skeletons and compendium data.

### Data and uninstall

HANGAR stores profiles, indexes and caches under
`%LOCALAPPDATA%\ACBridge\HANGAR\data`. Uninstalling or deleting the extracted
folder does not remove that profile automatically.

## 中文

### 系统要求

- Windows 10 或 Windows 11 64 位。
- 自己合法持有的 PC 版《装甲核心 VI：境界天火》。
- 若本机尚无 WitchyBND 或 .NET 10 Desktop Runtime，首次准备时需要联网。

### 首次使用

1. 将完整 ZIP 解压到普通可写目录，不要直接在 ZIP 内运行。
2. 双击 `HANGAR.exe`。程序会启动本地服务并打开专属窗口，无需接触 CMD。
3. 若导入向导提示组件缺失，点击 **一键安装缺失组件**。助手会复用已有组件，
   仅从 WitchyBND 官方 GitHub Release 下载，校验固定 SHA-256，并重新检测 .NET。
4. 若仍缺少 **Microsoft .NET 10 Desktop Runtime (x64)**，请安装
   [官方运行时](https://dotnet.microsoft.com/download/dotnet/10.0) 后重试。
5. 在导入向导选择 **Game Root**，选择 AC6 安装根目录或其中的 `Game` 子目录。
   **Expanded** 模式无需解包工具。
6. 等待解包与索引完成。游戏原始文件只读，不会被修改。
7. 正常关闭 HANGAR 窗口后，本次启动的后台服务会停止。

已有展开资源时，可改选 **Expanded**，直接指定包含部件、动作、骨架及 compendium
的资源目录。

### 数据与卸载

用户配置、索引和缓存保存在
`%LOCALAPPDATA%\ACBridge\HANGAR\data`。删除解压目录不会自动删除该用户数据目录。
