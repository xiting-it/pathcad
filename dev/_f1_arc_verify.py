"""复核圆弧 vs 直线的耗时（排除测试误差）。

上一轮测出 G02 比 64 段小直线慢 2.8 倍，这个结论反直觉，需要排除：
  - 应答延迟差异（圆弧只回 1 次，直线回 64 次，直线应该更慢才对）
  - 不同 F 值下的表现
  - 弦高误差：微线段逼近的精度
"""

from __future__ import annotations

import math
import time

from _f1_full_test import Board


def time_it(fn):
    t0 = time.time()
    fn()
    return time.time() - t0


def main():
    b = Board()

    print("=" * 66)
    print("复核：G02 圆弧 vs 直线离散，各 F 值下的耗时")
    print("=" * 66)
    print("半径 10 的整圆，周长 62.83mm")
    print()

    print(f"{'F':>6} {'G02 圆弧':>10} {'32段直线':>10} {'64段直线':>10} {'128段直线':>11}")
    print("-" * 66)

    results = []
    for feed in [300, 600, 1200, 3000]:
        # A: 4 段圆弧
        b.cmd("G00 X0 Y0", "原点", timeout=30)
        b.cmd("G00 X10 Y0", "起点", timeout=30)

        def run_arc():
            for g in ["G02 X0 Y10 I-10 J0 F%d" % feed,
                      "G02 X-10 Y0 I0 J-10 F%d" % feed,
                      "G02 X0 Y-10 I10 J0 F%d" % feed,
                      "G02 X10 Y0 I0 J10 F%d" % feed]:
                b.cmd(g, "", timeout=120, wait_extra=0)

        t_arc = time_it(run_arc)

        def run_lines(n):
            b.cmd("G00 X0 Y0", "原点", timeout=30)
            b.cmd("G00 X10 Y0", "起点", timeout=30)
            t0 = time.time()
            for i in range(1, n + 1):
                a = 2 * math.pi * i / n
                b.cmd(f"G01 X{10*math.cos(a):.3f} Y{10*math.sin(a):.3f} F{feed}",
                      "", timeout=60, wait_extra=0)
            return time.time() - t0

        t32 = run_lines(32)
        t64 = run_lines(64)
        t128 = run_lines(128)

        print(f"{feed:6d} {t_arc:9.2f}s {t32:9.2f}s {t64:9.2f}s {t128:10.2f}s")
        results.append((feed, t_arc, t32, t64, t128))

    print()
    print("=" * 66)
    print("理论耗时（周长 / 速度）")
    print("=" * 66)
    circ = 2 * math.pi * 10
    for feed, t_arc, t32, t64, t128 in results:
        theo = circ / (feed / 60)
        print(f"  F{feed:5d}: 理论 {theo:6.2f}s | 圆弧 {t_arc:6.2f}s ({t_arc/theo:5.2f}×) "
              f"| 32段 {t32:6.2f}s ({t32/theo:5.2f}×) "
              f"| 64段 {t64:6.2f}s ({t64/theo:5.2f}×)")

    print()
    print("=" * 66)
    print("附加：圆弧中途能否查询位置？（判断是否阻塞）")
    print("=" * 66)
    b.cmd("G00 X0 Y0", "原点", timeout=30)
    b.cmd("G00 X10 Y0", "起点", timeout=30)
    print("发一条很长的圆弧，立刻尝试 M50…")
    b.ser.reset_input_buffer()
    b.ser.write(b"@G02 X-10 Y0 I-10 J0 F60\r".replace(b"*", b"*").replace(b"\r", b"*FFFF\r"))
    b.ser.flush()
    time.sleep(0.5)
    r = b.cmd("M50", "运动中查询", timeout=3, wait_extra=0)
    print(f"  运动中的 M50 -> {r.resp}")
    if r.resp.startswith("E07"):
        print("  → 运动期间板子忙，不接受新指令（严格一问一答）")
    elif r.resp.startswith("D "):
        print("  → 运动期间可以查询状态")

    # 等它跑完
    print("  等待圆弧完成…")
    b.ser.timeout = 120
    d = b.ser.read_until(b"\r")
    print(f"  最终应答: {d[:-1].decode('ascii','replace') if d else '(超时)'}")

    b.cmd("G00 X0 Y0 Z0", "回原点", timeout=30)
    final = b.status()
    print(f"\n最终: {final['raw']}")
    b.close()


if __name__ == "__main__":
    main()
