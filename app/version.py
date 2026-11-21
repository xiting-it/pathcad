"""CAM-B 版本与产品标识 —— 唯一数据源。

改版本或改产品名只改这里，以下位置自动跟着变：
  1. 打包产物文件名      CAM-B-<版本>-dist.exe
  2. 窗口标题 / 状态栏 / 关于框
  3. 启动横幅 / 日志 / FastAPI 元信息 / /api/version
  4. dist/README.txt 与文档标题

版本格式：标准 SemVer（https://semver.org/lang/zh-CN/）
    MAJOR.MINOR.PATCH[-预发布标识][+构建元数据]

  MAJOR  不兼容的结构性变更（工程文件格式变了、接口签名变了）
  MINOR  向下兼容的功能新增
  PATCH  向下兼容的问题修正

  预发布标识：alpha / beta / rc，后接点号和递增数字
    1.4.0-beta.1  第一个测试版
    1.4.0-rc.1    候选发布
    1.4.0         正式版

历史沿革：
  beta1.3.12 及以前为非标准写法（渠道前缀 + 三段数字），自 1.4.0 起改用 SemVer。
"""

from __future__ import annotations

import re

# ── 产品标识 ────────────────────────────────────────────

APP_NAME = "CAM-B"
APP_TITLE = "CAM-B · 路径工艺平台"
APP_DESC = "气动挤出 / 激光切割 / 激光打标 上位机"

# 工程文件里用来识别产品来源的标识（小写，写入 JSON）
APP_SLUG = "cam-b"

# 旧产品名，用于读取旧工程与旧配置时做兼容
LEGACY_NAMES = ("pathCAM", "pathcam", "pathcad")

# ── 版本 ────────────────────────────────────────────────

VERSION = "1.4.0-beta.2"

_SEMVER_RE = re.compile(
    r"^(?P<major>\d+)\.(?P<minor>\d+)\.(?P<patch>\d+)"
    r"(?:-(?P<pre>[0-9A-Za-z.\-]+))?"
    r"(?:\+(?P<build>[0-9A-Za-z.\-]+))?$"
)


def parse_version(v: str = VERSION) -> dict:
    """拆出 SemVer 各段。格式不对时抛 ValueError，避免悄悄产出坏版本号。"""
    m = _SEMVER_RE.match(v)
    if not m:
        raise ValueError(
            f"版本号不符合 SemVer 规范：{v!r}\n"
            "正确示例：1.4.0 / 1.4.0-beta.1 / 2.0.0-rc.2"
        )
    d = m.groupdict()
    return {
        "major": int(d["major"]),
        "minor": int(d["minor"]),
        "patch": int(d["patch"]),
        "prerelease": d["pre"] or "",
        "build": d["build"] or "",
        "is_prerelease": bool(d["pre"]),
    }


def version_tuple(v: str = VERSION) -> tuple[int, int, int]:
    """用于比较大小的三段数字。"""
    p = parse_version(v)
    return (p["major"], p["minor"], p["patch"])


def channel(v: str = VERSION) -> str:
    """渠道标识：beta / rc / alpha / release。"""
    p = parse_version(v)
    if not p["prerelease"]:
        return "release"
    for c in ("alpha", "beta", "rc"):
        if p["prerelease"].startswith(c):
            return c
    return "prerelease"


# 产物文件名：CAM-B-1.4.0-beta.1-dist
DIST_NAME = f"{APP_NAME}-{VERSION}-dist"

# 打包时 PyInstaller 内部用的名字（进程名，便于杀进程）
PROCESS_NAME = "CAM-B"

# 显示用
VERSION_DISPLAY = f"{APP_NAME} {VERSION}"

__all__ = [
    "APP_NAME",
    "APP_TITLE",
    "APP_DESC",
    "APP_SLUG",
    "LEGACY_NAMES",
    "VERSION",
    "DIST_NAME",
    "PROCESS_NAME",
    "VERSION_DISPLAY",
    "parse_version",
    "version_tuple",
    "channel",
]
