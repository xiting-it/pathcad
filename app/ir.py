"""结构化指令序列（IR）。

P3 的核心改动：编译器不再直接拼 G-code 字符串，而是产出**与控制器无关的
指令序列**；再由各方言把它翻译成具体格式。

为什么要这一层：
  - G-code 只是众多控制器格式之一。激光卡可能是 DLL 调用、可能是私有二进制包，
    直接在编译器里拼字符串就永远接不进去。
  - 把"要做什么"（IR）和"怎么说"（方言）分开后，加一种新硬件只需要写一个
    render 函数，不用碰几何和工艺。

IR 只表达语义，不含任何协议细节：
  - 不说 M121 / M3 / SET_PIN，只说「打开工具 T1」
  - 不说 G4 P0.12 / G4 P120，只说「等 120 毫秒」
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

# 指令种类
KIND_MODAL = "modal"        # 模态设置（G21/G90/M203…），text 里放原文
KIND_COMMENT = "comment"    # 注释，text
KIND_MOVE = "move"          # 移动：x/y/z/feed/rapid
KIND_TOOL_ON = "tool_on"    # 打开工具：tool_id/channel/level
KIND_TOOL_OFF = "tool_off"  # 关闭工具
KIND_DWELL = "dwell"        # 驻留：ms
KIND_WAIT = "wait"          # 等待运动停止
KIND_END = "end"            # 程序结束


@dataclass
class Instr:
    """一条指令。字段按 kind 取用，其余保持默认。"""

    kind: str

    # move
    x: float | None = None
    y: float | None = None
    z: float | None = None
    feed: float | None = None
    rapid: bool = False

    # tool
    tool_id: str | None = None
    channel: int | None = None
    level: int | None = None

    # dwell
    ms: int = 0

    # modal / comment
    text: str = ""

    def preview(self) -> str:
        """给人看的单行摘要，用于调试与「指令序列」面板。"""
        if self.kind == KIND_MOVE:
            axes = []
            if self.x is not None:
                axes.append(f"X{self.x:g}")
            if self.y is not None:
                axes.append(f"Y{self.y:g}")
            if self.z is not None:
                axes.append(f"Z{self.z:g}")
            head = "空驶" if self.rapid else "走线"
            tail = f" F{self.feed:g}" if self.feed else ""
            return f"{head} {' '.join(axes)}{tail}"
        if self.kind == KIND_TOOL_ON:
            return f"打开 {self.tool_id}（通道{self.channel}={self.level}）"
        if self.kind == KIND_TOOL_OFF:
            return f"关闭 {self.tool_id}（通道{self.channel}={self.level}）"
        if self.kind == KIND_DWELL:
            return f"驻留 {self.ms}ms"
        if self.kind == KIND_WAIT:
            return "等待运动停止"
        if self.kind == KIND_END:
            return "程序结束"
        return self.text


@dataclass
class Program:
    """一段完整的加工指令序列。"""

    instrs: list[Instr] = field(default_factory=list)

    # 统计信息（编译器填，界面直接用）
    stroke_count: int = 0
    path_length: float = 0.0
    tool_used: list[str] = field(default_factory=list)

    def append(self, ins: Instr) -> None:
        self.instrs.append(ins)

    def extend(self, items: list[Instr]) -> None:
        self.instrs.extend(items)

    def count(self, kind: str) -> int:
        return sum(1 for i in self.instrs if i.kind == kind)

    def dwell_total_ms(self) -> int:
        return sum(i.ms for i in self.instrs if i.kind == KIND_DWELL)


# ── 便捷构造 ────────────────────────────────────────────


def modal(text: str) -> Instr:
    return Instr(kind=KIND_MODAL, text=text)


def comment(text: str) -> Instr:
    return Instr(kind=KIND_COMMENT, text=text)


def move(*, x: float | None = None, y: float | None = None, z: float | None = None,
         feed: float | None = None, rapid: bool = False) -> Instr:
    return Instr(kind=KIND_MOVE, x=x, y=y, z=z, feed=feed, rapid=rapid)


def tool_on(*, tool_id: str, channel: int, level: int) -> Instr:
    return Instr(kind=KIND_TOOL_ON, tool_id=tool_id, channel=channel, level=level)


def tool_off(*, tool_id: str, channel: int, level: int) -> Instr:
    return Instr(kind=KIND_TOOL_OFF, tool_id=tool_id, channel=channel, level=level)


def dwell(ms: int) -> Instr:
    return Instr(kind=KIND_DWELL, ms=ms)


def wait() -> Instr:
    return Instr(kind=KIND_WAIT)


def end() -> Instr:
    return Instr(kind=KIND_END)


__all__ = [
    "Instr",
    "Program",
    "KIND_MODAL",
    "KIND_COMMENT",
    "KIND_MOVE",
    "KIND_TOOL_ON",
    "KIND_TOOL_OFF",
    "KIND_DWELL",
    "KIND_WAIT",
    "KIND_END",
    "modal",
    "comment",
    "move",
    "tool_on",
    "tool_off",
    "dwell",
    "wait",
    "end",
]
