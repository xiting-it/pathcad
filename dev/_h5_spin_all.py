"""四轴轮流连续转动，用来判断「只是发热」还是「堵转」。

原理：
  只动 3mm 的测试无效 —— 电机 0.6 秒就停了，进入「保持」状态，
  好坏都在满电流发热，分不出来。
  让它连续转 2 秒才有效：
    转起来          → 电机和接线没问题，热是负载/电流导致的
    不转但板子回 OK → 就是堵转（线圈配错 / 机械卡死 / 驱动异常）

第四轴字母：协议写 E、板子资料写 U。脚本先试 E，不行再试 U。

用法：
    python dev/_h5_spin_all.py
    python dev/_h5_spin_all.py --seconds 2 --feed 300 --pause 2
"""

from __future__ import annotations

import argparse
import re
import socket
import sys
import time

IP = "192.168.0.102"
PORT = 5000
LOCAL = "192.168.0.100"
CR = b"\r"
LETTERS = ("E", "B")

# 位置行：X:0.000 Y:0.000 Z:0.000 E:0.000
_POS_RE = re.compile(r"([XYZUE]):\s*(-?\d+(?:\.\d+)?)")


def is_code(t: str) -> bool:
    return len(t) == 4 and t[0] in LETTERS and t[1:].isdigit()


def is_ok(c: str) -> bool:
    return c in ("E000", "B000")


class H5:
    def __init__(self) -> None:
        self.s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.s.bind((LOCAL, 0))

    def close(self) -> None:
        self.s.close()

    def drain(self, wait: float = 0.15) -> None:
        self.s.settimeout(wait)
        end = time.time() + wait
        try:
            while time.time() < end:
                self.s.recvfrom(4096)
        except socket.timeout:
            pass

    def ask(self, line: str, timeout: float = 30.0) -> tuple[str, float]:
        """发一行，返回 (结果码, 耗时秒)。"""
        self.drain()
        self.s.settimeout(2.0)
        self.s.sendto(line.encode() + CR, (IP, PORT))
        t0 = time.perf_counter()
        while time.perf_counter() - t0 < timeout:
            try:
                data, _ = self.s.recvfrom(4096)
            except socket.timeout:
                continue
            for part in data.decode("ascii", "replace").split("\r"):
                part = part.strip()
                if part and is_code(part):
                    return part, time.perf_counter() - t0
        return "超时", time.perf_counter() - t0

    def pos(self) -> dict[str, float] | None:
        """读当前位置。

        两个坑：
          1. 结果包是 B000 + CR + X:.. Y:.. Z:.. E:.. + CR + ok，
             坐标和结果码在同一个包里。见到 B000 就 return 会把坐标丢掉。
          2. queued / echo / result 是三个独立的包，中间隔几十毫秒，
             所以必须收满一段时间再解析。
        """
        self.drain()
        self.s.settimeout(1.0)
        self.s.sendto(b"M114" + CR, (IP, PORT))

        buf = ""
        t0 = time.perf_counter()
        while time.perf_counter() - t0 < 6.0:
            try:
                data, _ = self.s.recvfrom(4096)
            except socket.timeout:
                if "B000" in buf or "E000" in buf:
                    break
                continue
            buf += data.decode("ascii", "replace")

        # 位置行格式是 `X:0.000 Y:0.000 Z:0.000 E:0.000`
        # 注意字母和数字之间有**冒号**，不能按空格切（切出来带冒号匹配不上）
        for part in buf.split("\r"):
            part = part.strip()
            if not part.startswith("X:"):
                continue
            out = {k: float(v) for k, v in _POS_RE.findall(part)}
            if out:
                return out
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=2.0, help="每轴持续转动秒数")
    ap.add_argument("--feed", type=int, default=300, help="进给 mm/min")
    ap.add_argument("--pause", type=float, default=2.0, help="轴间停顿秒数")
    args = ap.parse_args()

    mm_per_s = args.feed / 60.0
    dist = round(mm_per_s * args.seconds, 2)

    h = H5()
    start = h.pos()
    if not start:
        print("[!!] 板子没回位置 —— 先检查网线和供电。")
        h.close()
        return 1

    print("=" * 66)
    print(" 四轴轮流连续转动 —— 判断是「发热」还是「堵转」")
    print("=" * 66)
    print(f" 起点    {start}")
    print(f" 每轴   连续转 {args.seconds:g} 秒")
    print(f"         速度 F{args.feed} = {mm_per_s:g} mm/s")
    print(f"         所以每次走 {dist:g} mm")
    print(f" 间隔   停 {args.pause:g} 秒")
    print()
    print(" 开环系统：板子不知道电机有没有真转。")
    print(" 每轴走完请立刻看：转了吗？烫不烫？")
    print()

    # 先确认第四轴用哪个字母
    fourth = None
    for letter in ("E", "U"):
        test = f"G1 {letter}0.01 F300"
        c, _ = h.ask(test, timeout=8)
        print(f" 第四轴字母测试  {test:22s} → {c}")
        if is_ok(c):
            fourth = letter
            break
    print(f" → 第四轴用字母：{fourth or '都不支持'}")
    print()

    axes = ["X", "Y", "Z"] + ([fourth] if fourth else [])
    results: list[tuple[str, str, float]] = []

    for i, ax in enumerate(axes):
        target = start.get(ax, 0.0) + dist
        cmd = f"G1 {ax}{target:.2f} F{args.feed}"
        print(f"--- [{i + 1}/{len(axes)}] {ax} 轴 —— {cmd}")
        print(f"    >>> 现在开始转，请盯着第 {i + 1} 个电机")
        code, dt = h.ask(cmd, timeout=args.seconds + 15)
        expect_ms = args.seconds * 1000 + 150
        print(f"    板子回 {code}   用时 {dt * 1000:.0f} ms"
              f"（预期 ≈ {expect_ms:.0f} ms）")

        now = h.pos()
        if now and ax in now:
            print(f"    {ax} 坐标 = {now[ax]:.2f}")
        results.append((ax, code, dt))

        print("    >>> 摸一下这个电机：烫不烫？刚才转了吗？")
        print()
        time.sleep(args.pause)

    print("--- 回起点")
    for ax in axes:
        tgt = start.get(ax, 0.0)
        code, _ = h.ask(f"G1 {ax}{tgt:.2f} F{args.feed}", timeout=args.seconds + 15)
        print(f"    {ax} → {tgt:.2f}    {code}")
    h.ask("M400", timeout=30)

    print()
    print("=" * 66)
    print(" 请对照填写")
    print("=" * 66)
    print()
    print("  轴  指令耗时   板子回码   实际转了吗   手感温度   对应接口")
    print("  ---  ---------  ---------  -----------  ---------  --------")
    for ax, code, sec in results:
        print(f"  {ax:3s}  {sec:6.2f} s   {code:9s}  {'?':^11}  {'?':^9}  {'?':^8}")
    print()
    print(" 判断方法：")
    print("   · 转起来了、只是温  → 正常，热来自负载或电流偏大")
    print("   · 转起来了、但很烫  → 电流设太大，调低驱动电流")
    print("   · 没转、板子还回 OK → 堵转：线圈配错 / 机械卡死 / 驱动异常")
    print("   · 没转、回非 OK     → 板子层面就没执行")

    h.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
