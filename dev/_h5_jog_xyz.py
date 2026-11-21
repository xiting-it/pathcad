"""逐个轴点动，用来肉眼看哪个电机在转。

安全设计：
  · 每次只动**一个轴**，动完停 2.5 秒，让你有足够时间盯着看
  · 距离只有 3mm、速度 F300（5mm/s），万一是堵转也不会立刻过热
  · 全部走完自动回到起点，不会让机器漂走
  · Z 轴只往**上**动，不会往下扎

用法：
    python dev/_h5_jog_xyz.py
    python dev/_h5_jog_xyz.py --dist 5 --feed 300 --axis X Y Z
"""

from __future__ import annotations

import argparse
import socket
import sys
import time

IP = "192.168.0.102"
PORT = 5000
LOCAL = "192.168.0.100"
CR = b"\r"

# H5：G 行回 E 码，纯 M 行回 B 码
LETTERS = ("E", "B")


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

    def drain(self, wait: float = 0.18) -> None:
        self.s.settimeout(wait)
        end = time.time() + wait
        try:
            while time.time() < end:
                self.s.recvfrom(4096)
        except socket.timeout:
            pass

    def ask(self, line: str, timeout: float = 20.0) -> tuple[str, list[str]]:
        self.drain()
        self.s.settimeout(2.5)
        self.s.sendto(line.encode() + CR, (IP, PORT))
        rows: list[str] = []
        t0 = time.perf_counter()
        while time.perf_counter() - t0 < timeout:
            try:
                data, _ = self.s.recvfrom(4096)
            except socket.timeout:
                continue
            for part in data.decode("ascii", "replace").split("\r"):
                part = part.strip()
                if not part:
                    continue
                rows.append(part)
                if is_code(part):
                    return part, rows
        return "超时", rows

    def pos(self) -> dict[str, float] | None:
        """读当前位置。"""
        code, rows = self.ask("M114", timeout=8)
        if not is_ok(code):
            return None
        for r in rows:
            if r.startswith("X:"):
                out: dict[str, float] = {}
                for tok in r.replace(":", " ").split():
                    if tok and tok[0] in "XYZUE" and len(tok) > 1:
                        try:
                            out[tok[0]] = float(tok[1:])
                        except ValueError:
                            pass
                return out
        return None


def fmt(p: dict[str, float] | None) -> str:
    if not p:
        return "(读不到位置)"
    return " ".join(f"{k}={v:.2f}" for k, v in p.items())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dist", type=float, default=3.0, help="每次移动距离 mm")
    ap.add_argument("--feed", type=int, default=300, help="进给 mm/min")
    ap.add_argument("--axis", nargs="*", default=["X", "Y", "Z"], help="要测的轴")
    ap.add_argument("--pause", type=float, default=2.5, help="每步后的观察停留秒数")
    args = ap.parse_args()

    axes = [a.upper() for a in args.axis if a.upper() in ("X", "Y", "Z", "U", "E")]
    if not axes:
        print("没有要测的轴")
        return 1

    h = H5()

    # 先确认通信
    start = h.pos()
    if not start:
        print("[!!] 板子没回位置，先检查网线和供电。")
        h.close()
        return 1

    print("=" * 62)
    print(" 逐个轴点动 —— 请盯着电机，看哪一/几个在转")
    print("=" * 62)
    print(f" 起点位置  {fmt(start)}")
    print(f" 参数：每轴 +{args.dist:g}mm，速度 F{args.feed}，"
          f"每步后停 {args.pause:g} 秒")
    print()
    print(" ⚠️ 步进电机是开环的：**板子不知道电机有没有真转**，")
    print("    所以回读坐标一定会变，只有你的眼睛能判断。")
    print()

    moved: dict[str, tuple[float, float]] = {}
    seq = axes + axes[::-1]  # 去程 + 回程

    for i, ax in enumerate(seq):
        outbound = i < len(axes)
        base = start
        # 去程：从起点 +dist；回程：回起点
        target = base.get(ax, 0.0) + (args.dist if outbound else 0.0)

        tag = "去程" if outbound else "回程"
        print(f"--- [{i + 1}/{len(seq)}] {ax} 轴 {tag} "
              f"→ {ax}{target:.2f} " + "-" * 20)

        cmd = f"G1 {ax}{target:.2f} F{args.feed}"
        t0 = time.perf_counter()
        code, rows = h.ask(cmd, timeout=25)
        dt = (time.perf_counter() - t0) * 1000

        if is_ok(code):
            print(f"     板子回 {code}  用时 {dt:.0f} ms")
        else:
            print(f"     [!!] 板子回 {code}  ← 这条没执行成功")

        time.sleep(0.4)
        now = h.pos()
        if now:
            print(f"     坐标 {fmt(now)}")
            if outbound:
                moved[ax] = (start.get(ax, 0.0), now.get(ax, 0.0))

        # 停下来让你看
        print(f"     >>> 请看 {args.pause:g} 秒：{ax} 轴刚才动了吗？\n")
        time.sleep(args.pause)

    h.ask("M400", timeout=30)
    final = h.pos()

    print("=" * 62)
    print(" 完成。请对照填写：")
    print("=" * 62)
    print()
    print("  轴   板子报告的位移    实际电机有没有动   对应哪个接口")
    print("  ----  ----------------  -----------------  ------------")
    for ax in axes:
        a, b = moved.get(ax, (0.0, 0.0))
        print(f"  {ax:4s}  {b - a:+8.2f} mm       {'?':^17}  {'?':^12}")
    print()
    print(f"  最终位置  {fmt(final)}")
    print()
    print("  提示：如果某个轴的位移是 0 或者回码异常，说明板子层面就没执行。")
    print("        如果位移正常但你看到电机没动，那就是电机/接线/电流的问题。")

    h.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
