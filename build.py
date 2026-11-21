"""CAM-B 打包脚本。

用法：
    python build.py

产物：dist/CAM-B-<版本>-dist.exe（单文件，双击即用）
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DIST = ROOT / "dist"
BUILD = ROOT / "build"
SPEC = ROOT / "CAM-B.spec"

NAME = "CAM-B"            # PyInstaller 内部名（进程名）

try:
    from app.version import APP_NAME, DIST_NAME, PROCESS_NAME, VERSION
except Exception:                      # 打包环境里也能跑
    APP_NAME = "CAM-B"
    VERSION = "dev"
    DIST_NAME = "CAM-B-dev-dist"
    PROCESS_NAME = "CAM-B"


def _kill_running() -> None:
    """杀掉正在跑的实例（产物名带版本号，用通配匹配）。"""
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-Process | Where-Object {$_.Name -like '*CAM-B*'} | "
             "Select-Object -ExpandProperty Id"],
            capture_output=True, text=True, timeout=15,
        )
        for pid in (out.stdout or "").split():
            if pid.strip().isdigit():
                subprocess.run(["taskkill", "/F", "/PID", pid.strip()],
                               capture_output=True, timeout=10)
                print(f"  已结束旧进程 PID {pid.strip()}")
    except Exception as e:
        print(f"  [警告] 检查旧进程失败：{e}")


def clean():
    _kill_running()
    for d in (DIST, BUILD):
        if d.exists():
            print(f"清理 {d.name}/")
            shutil.rmtree(d, ignore_errors=True)
    if SPEC.exists():
        SPEC.unlink()


def make_icon() -> Path | None:
    """生成一个简单的 .ico（不需要外部素材）。"""
    try:
        from PIL import Image, ImageDraw
    except ImportError:
        print("跳过图标（没装 pillow）")
        return None

    ico = ROOT / "build_icon.ico"
    size = 256
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    # 圆角底板
    d.rounded_rectangle([8, 8, size - 8, size - 8], radius=52, fill=(23, 27, 33, 255))
    # 三条蓝色路径（象征打印路径）
    col = (78, 163, 255, 255)
    for i, y in enumerate((86, 128, 170)):
        d.line([(52, y), (128, y), (128, y + 30), (204, y + 30)], fill=col, width=13, joint="curve")
    # 起点绿点 / 终点橙点
    d.ellipse([44, 78, 62, 96], fill=(90, 209, 154, 255))
    d.ellipse([196, 192, 214, 210], fill=(255, 180, 84, 255))
    img.save(ico, format="ICO", sizes=[(256, 256), (128, 128), (64, 64), (32, 32), (16, 16)])
    print(f"生成图标 {ico.name}")
    return ico


def build():
    clean()
    icon = make_icon()

    args = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--onefile",
        "--name", NAME,
        "--add-data", f"web{os.pathsep}web",
        "--add-data", f"docs{os.pathsep}docs",
        "--add-data", f"_manual{os.pathsep}_manual",
        "--hidden-import", "uvicorn.logging",
        "--hidden-import", "uvicorn.loops",
        "--hidden-import", "uvicorn.loops.auto",
        "--hidden-import", "uvicorn.protocols",
        "--hidden-import", "uvicorn.protocols.http",
        "--hidden-import", "uvicorn.protocols.http.auto",
        "--hidden-import", "uvicorn.protocols.websockets",
        "--hidden-import", "uvicorn.protocols.websockets.auto",
        "--hidden-import", "uvicorn.lifespan",
        "--hidden-import", "uvicorn.lifespan.on",
        "--hidden-import", "app.server",
        "--hidden-import", "app.link",
        "--hidden-import", "app.mesh",
        "--hidden-import", "app.svg_import",
        "--hidden-import", "shapely",
        "--collect-submodules", "shapely",
        "main.py",
    ]
    if icon:
        args[args.index("--name"):args.index("--name")] = []
        args += ["--icon", str(icon)]

    print("\n开始打包…\n")
    r = subprocess.run(args, cwd=str(ROOT))
    if r.returncode != 0:
        print("\n打包失败")
        return r.returncode

    exe = DIST / f"{NAME}.exe"
    if not exe.exists():
        print("\n[警告] 没找到产物")
        return 1

    # 重命名成带版本号的交付名：CAM-B-1.4.0-beta.1-dist.exe
    final = DIST / f"{DIST_NAME}.exe"
    if final.exists():
        final.unlink()
    exe.rename(final)

    mb = final.stat().st_size / 1024 / 1024
    print(f"\n[OK] 打包完成：{final}")
    print(f"     版本 {VERSION}    大小 {mb:.1f} MB")
    print("\n把这个文件拷给别人，双击即可运行。")
    return 0


if __name__ == "__main__":
    raise SystemExit(build())
