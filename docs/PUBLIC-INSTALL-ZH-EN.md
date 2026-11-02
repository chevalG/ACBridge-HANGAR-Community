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
2. Run `INSTALL-GAME-TOOLS.cmd`.
3. If the assistant reports a missing .NET runtime, install **Microsoft .NET 10
   Desktop Runtime (x64)** from
   <https://dotnet.microsoft.com/download/dotnet/10.0>, then rerun the assistant.
4. Accept the optional WitchyBND download from its official GitHub release. The
   assistant verifies the pinned archive SHA-256 before installing it.
5. Run `START.cmd`.
6. In the import wizard, choose **Game Root** and select the AC6 installation
   folder or its `Game` subfolder.
7. Wait for extraction and indexing. The source game files remain read-only.

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
2. 双击 `INSTALL-GAME-TOOLS.cmd`。
3. 若提示缺少 .NET，请安装
   [Microsoft .NET 10 Desktop Runtime (x64)](https://dotnet.microsoft.com/download/dotnet/10.0)，
   然后重新运行助手。
4. 按提示从 WitchyBND 官方 GitHub Release 获取工具；助手会先校验固定版本的
   SHA-256，再复制到 HANGAR 工具目录。
5. 双击 `START.cmd`。
6. 在导入向导选择 **Game Root**，选择 AC6 安装根目录或其中的 `Game` 子目录。
7. 等待解包与索引完成。游戏原始文件只读，不会被修改。

已有展开资源时，可改选 **Expanded**，直接指定包含部件、动作、骨架及 compendium
的资源目录。

### 数据与卸载

用户配置、索引和缓存保存在
`%LOCALAPPDATA%\ACBridge\HANGAR\data`。删除解压目录不会自动删除该用户数据目录。
