"""ACBridge HANGAR - AC6 解包工具链一键检测与安装助手。

因第三方解包工具 WitchyBND 内含专有商用压缩库（oo2core_8_win64.dll），
为遵循软件分发合规与版权规范，HANGAR 官方发行包不直接内嵌 WitchyBND 二进制文件。
本脚本可协助用户一键从官方开源发布页下载 WitchyBND，或自动链接本机已有的 WitchyBND 安装。
"""
import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from hangar.engine import Engine

WITCHY_VERSION = "3.0.1.0"
WITCHY_RELEASE_URL = (
    "https://github.com/ividyon/WitchyBND/releases/download/"
    "3.0.1.0/WitchyBND-3.0.1.0-win-x64.zip"
)
WITCHY_ZIP_SHA256 = (
    "a3e6b2a0f7eac13f5e83b6602a1149322439c0662baa140ecdd84be28af50364"
)
DOTNET_DESKTOP_DOWNLOAD_URL = (
    "https://dotnet.microsoft.com/download/dotnet/10.0"
)

COMMON_WITCHY_SEARCH_PATHS = [
    Path(os.environ.get("USERPROFILE", "")) / "Downloads" / "WitchyBND",
    Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "WitchyBND",
    Path("C:/tools/WitchyBND"),
    Path("D:/tools/WitchyBND"),
    Path("E:/tools/WitchyBND"),
]


def probe_toolchain():
    engine = Engine(ROOT, ROOT / "data")
    return engine.game_toolchain()


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def safe_extract_zip(archive, destination):
    """Extract a trusted, hash-verified release without path traversal."""
    destination = Path(destination).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(archive, "r") as bundle:
        for member in bundle.infolist():
            target = (destination / member.filename).resolve()
            if not target.is_relative_to(destination):
                raise ValueError(f"unsafe archive path: {member.filename}")
        bundle.extractall(destination)


def install_witchy_from_folder(src_folder: Path, dst_folder: Path):
    exe = src_folder / "WitchyBND.exe"
    if not exe.is_file():
        return False
    dst_folder.mkdir(parents=True, exist_ok=True)
    for item in src_folder.iterdir():
        if item.is_file():
            shutil.copy2(item, dst_folder / item.name)
        elif item.is_dir() and item.name in ("Assets", "lib"):
            shutil.copytree(item, dst_folder / item.name, dirs_exist_ok=True)
    # WitchyBND needs the Oodle runtime to unpack AC6's DCX_KRAK parts.
    # The runtime is not redistributed by HANGAR; the user supplies it from
    # their own game installation or an existing copy beside WitchyBND.
    for candidate in src_folder.glob("oo2core*_win64.dll"):
        shutil.copy2(candidate, dst_folder / candidate.name)
    return (dst_folder / "WitchyBND.exe").is_file()


def link_oodle_from_game(game_dir: Path, dst_folder: Path):
    """Copy the user's own Oodle runtime beside WitchyBND.

    Returns the copied path, or None when the game does not contain the DLL.
    HANGAR never downloads or redistributes this proprietary component.
    """
    game_dir = Path(game_dir)
    candidates = [
        game_dir / "oo2core_8_win64.dll",
        game_dir / "Game" / "oo2core_8_win64.dll",
    ]
    for candidate in candidates:
        if candidate.is_file():
            dst_folder.mkdir(parents=True, exist_ok=True)
            target = dst_folder / candidate.name
            if not target.is_file() or target.stat().st_size != candidate.stat().st_size:
                shutil.copy2(candidate, target)
            return target
    return None


def download_and_extract_witchy(dst_folder: Path):
    print(
        "正在从 WitchyBND 官方 GitHub 发布页下载固定版本 "
        f"{WITCHY_VERSION}...\nURL: {WITCHY_RELEASE_URL}"
    )
    dst_folder.mkdir(parents=True, exist_ok=True)
    zip_temp = None
    staging = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix="witchybnd-", suffix=".zip", delete=False
        ) as stream:
            zip_temp = Path(stream.name)
        urllib.request.urlretrieve(WITCHY_RELEASE_URL, zip_temp)
        actual = sha256_file(zip_temp)
        if actual.lower() != WITCHY_ZIP_SHA256:
            raise ValueError(
                "下载文件校验失败，已拒绝安装。"
                f" expected={WITCHY_ZIP_SHA256} actual={actual}"
            )
        print("下载与 SHA-256 校验完成，正在解包至 runtime/tools/witchy/ ...")
        staging = Path(tempfile.mkdtemp(prefix=".witchybnd-", dir=dst_folder.parent))
        safe_extract_zip(zip_temp, staging)
        source_exe = next(staging.rglob("WitchyBND.exe"), None)
        if source_exe is None:
            raise FileNotFoundError("官方压缩包中未找到 WitchyBND.exe")
        source_root = source_exe.parent
        for item in source_root.iterdir():
            target = dst_folder / item.name
            if item.is_dir():
                shutil.copytree(item, target, dirs_exist_ok=True)
            elif item.is_file():
                shutil.copy2(item, target)
        return (dst_folder / "WitchyBND.exe").is_file()
    except Exception as ex:
        print(f"下载或解包失败: {ex}")
        return False
    finally:
        if zip_temp and zip_temp.is_file():
            try:
                zip_temp.unlink()
            except OSError:
                pass
        if staging and staging.is_dir():
            shutil.rmtree(staging, ignore_errors=True)


def dotnet_desktop_10_ready():
    candidates = [shutil.which("dotnet")]
    candidates.extend([
        Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "dotnet" / "dotnet.exe",
        Path(os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)")) /
        "dotnet" / "dotnet.exe",
    ])
    for candidate in candidates:
        if not candidate or not Path(candidate).is_file():
            continue
        try:
            result = subprocess.run(
                [str(candidate), "--list-runtimes"],
                capture_output=True, text=True, timeout=15, check=False,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        for line in result.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[0] == "Microsoft.WindowsDesktop.App" \
                    and parts[1].startswith("10."):
                return True
    return False


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--witchy-path", type=str, help="指定已解压的 WitchyBND 目录或 WitchyBND.exe 完整路径")
    parser.add_argument("--auto-download", action="store_true", help="若未在本地找到 WitchyBND，自动从 GitHub 下载")
    parser.add_argument("--game-dir", type=str, help="从用户合法安装的 AC6 目录复用 oo2core Oodle 运行时")
    args = parser.parse_args(argv)

    print("==================================================")
    print("  ACBridge HANGAR - AC6 解包工具链安装助手")
    print("==================================================")

    current = probe_toolchain()
    if current["ready"] and not args.game_dir:
        print("\n[OK] 本地解包工具链已完整就绪：")
        print(f"  - NuxeBridge: {current['nuxe_bridge']}")
        print(f"  - Nuxe 资源索引: {current['nuxe_res']}")
        print(f"  - WitchyBND:  {current['witchy']}")
        print("\n您可以在 HANGAR 首次导入向导中直接选择 AC6 游戏安装目录！")
        return 0

    print(f"\n当前缺失组件: {', '.join(current['missing'])}")
    dst_witchy = ROOT / "runtime" / "tools" / "witchy"

    if not current["witchy"]:
        installed = False
        # 1. 检查用户显式传入的路径
        if args.witchy_path:
            wp = Path(args.witchy_path)
            src_dir = wp.parent if wp.is_file() else wp
            if (src_dir / "WitchyBND.exe").is_file():
                print(f"正在从用户指定路径复制 WitchyBND: {src_dir}")
                installed = install_witchy_from_folder(src_dir, dst_witchy)

        # 2. 自动检查本机常见路径
        if not installed:
            for cand in COMMON_WITCHY_SEARCH_PATHS:
                if (cand / "WitchyBND.exe").is_file():
                    print(f"检测到本机已存在的 WitchyBND 安装: {cand}")
                    installed = install_witchy_from_folder(cand, dst_witchy)
                    if installed:
                        break

        # 3. 自动从官方 GitHub 下载
        if not installed:
            if args.auto_download or (
                sys.stdin.isatty()
                and input(
                    f"是否自动从官方 GitHub 下载 WitchyBND {WITCHY_VERSION}? (y/N): "
                ).strip().lower() == "y"
            ):
                installed = download_and_extract_witchy(dst_witchy)

        if not installed:
            print("\n[提示] 未能自动安装 WitchyBND。您可以：")
            print("  1. 手动下载 WitchyBND (https://github.com/ividyon/WitchyBND/releases)")
            print(f"  2. 将解包文件解压至: {dst_witchy}")
            print("  3. 或在导入向导中直接使用「展开资源目录 (Expanded)」模式选择已导出的 parts/clips 目录。")

    if not dotnet_desktop_10_ready():
        print("\n[缺少运行时] 安装 Microsoft .NET 10 Desktop Runtime 后才能使用 Game Root：")
        print(f"  {DOTNET_DESKTOP_DOWNLOAD_URL}")

    if args.game_dir:
        copied = link_oodle_from_game(Path(args.game_dir), dst_witchy)
        if copied:
            print(f"\n[OK] 已从用户游戏目录复用 Oodle 运行时: {copied}")
        else:
            print("\n[提示] 未在指定游戏目录找到 oo2core_8_win64.dll；"
                  "KRAK 压缩的部件包将无法解包。")

    after = probe_toolchain()
    if after["ready"]:
        print("\n==================================================")
        print("[成功] 所有解包组件已就绪！")
        print(f"  - NuxeBridge: {after['nuxe_bridge']}")
        print(f"  - Nuxe 资源索引: {after['nuxe_res']}")
        print(f"  - WitchyBND:  {after['witchy']}")
        print("==================================================")
        return 0
    else:
        print(f"\n[待完成] 仍缺少组件: {', '.join(after['missing'])}")
        if not dotnet_desktop_10_ready() and \
                "Microsoft .NET 10 Desktop Runtime" not in after["missing"]:
            print("  - Microsoft .NET 10 Desktop Runtime")
        return 1


if __name__ == "__main__":
    sys.exit(main())
