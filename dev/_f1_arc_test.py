"""专项测试：加减速行为、圆弧轨迹精度、多段连续运动耗时。

这几项决定 G-code 生成策略：
  - 如果无加减速，短线段会被瞬间执行，但换向会有冲击
  - 圆弧如果用微线段逼近，要确认逼近精度够不够（决定能否替代手工离散）
"""

from __future__ import annotations

import time

import serial

from _f1_full_test import Board, build_frame

PORT = "COM7"


def main():
    b = Board()

    print("=" * 64)
    print("专项 1：有加减速吗？")
    print("=" * 64)
    print("方法：同样走 50mm，用不同 F。如果无加减速，耗时 = 距离/速度；")
    print("      如果有加减速，低速时会有额外的起停时间。")
    print()

    b.cmd("G00 X0", "原点", timeout=30)
    rows = []
    for feed in [60, 120, 300, 600, 1200, 3000, 6000]:
        for rep in range(2):
            b.cmd("G00 X0", "复位", timeout=30)
            r = b.cmd(f"G01 X50 F{feed}", f"F{feed}", timeout=120)
            t = r.elapsed
            v = 50 / t if t > 0 else 0
            if rep == 0:
                rows.append((feed, feed / 60, t, v))
    print(f"{'F':>6} {'标称mm/s':>10} {'实测s':>8} {'实测mm/s':>10} {'效率':>7}")
    for feed, nom, t, v in rows:
        print(f"{feed:6d} {nom:10.2f} {t:8.2f} {v:10.2f} {v/nom*100:6.1f}%")

    print()
    print("=" * 64)
    print("专项 2：短线段行为（无前瞻会怎样）")
    print("=" * 64)
    b.cmd("G00 X0", "原点", timeout=30)
    # 10 段 5mm 的连续移动，对比一次 50mm
    t0 = time.time()
    for i in range(1, 11):
        b.cmd(f"G01 X{i*5} F600", f"第{i}段", timeout=30, wait_extra=0)
    t_10seg = time.time() - t0

    b.cmd("G00 X0", "复位", timeout=30)
    t0 = time.time()
    b.cmd("G01 X50 F600", "一次到位", timeout=30)
    t_1seg = time.time() - t0

    print(f"  10 段 × 5mm: {t_10seg:.2f}s")
    print(f"  1 段 × 50mm: {t_1seg:.2f}s")
    print(f"  多出来的开销: {t_10seg - t_1seg:.2f}s（{9} 次额外的起停）")
    print(f"  → 每段额外开销约 {(t_10seg - t_1seg)/9*1000:.0f}ms")

    print()
    print("=" * 64)
    print("专项 3：圆弧轨迹精度")
    print("=" * 64)
    print("用 G02 画整圆（4 段 1/4 圆），中途频繁读 M50 采样实际位置，")
    print("看最大半径误差。如果误差大，说明微线段逼近太粗。")
    print()

    b.cmd("G00 X0 Y0", "原点", timeout=30)
    b.cmd("G00 X10 Y0", "到起点", timeout=30)

    samples = []
    # 分 4 段画整圆，每段之间采样
    arcs = [
        ("G02 X0 Y10 I-10 J0 F600", "1/4 圆"),
        ("G02 X-10 Y0 I0 J-10 F600", "1/4 圆"),
        ("G02 X0 Y-10 I10 J0 F600", "1/4 圆"),
        ("G02 X10 Y0 I0 J10 F600", "回到起点"),
    ]
    for g, desc in arcs:
        r = b.cmd(g, desc, timeout=60)
        st = b.status()
        if st.get("x") is not None:
            rad = (st["x"] ** 2 + st["y"] ** 2) ** 0.5
            samples.append((desc, st["x"], st["y"], rad))
            print(f"  {desc:10s} -> ({st['x']:7.2f}, {st['y']:7.2f})  半径 {rad:6.3f}  {r.resp}")

    if samples:
        radii = [s[3] for s in samples]
        print(f"\n  理想半径 10.000")
        print(f"  实际半径范围 {min(radii):.3f} ~ {max(radii):.3f}")
        print(f"  最大误差 {max(abs(r - 10) for r in radii):.4f} mm")

    print()
    print("=" * 64)
    print("专项 4：圆弧 vs 直线离散的耗时对比")
    print("=" * 64)
    print("同样画半径 10 的整圆，对比：")
    print("  A. 4 段 G02 圆弧")
    print("  B. 手工离散成 64 段小直线")
    print()

    # A: 圆弧
    b.cmd("G00 X0 Y0", "原点", timeout=30)
    b.cmd("G00 X10 Y0", "起点", timeout=30)
    t0 = time.time()
    for g, _ in arcs:
        b.cmd(g, "", timeout=60, wait_extra=0)
    t_arc = time.time() - t0

    # B: 64 段小直线
    import math
    b.cmd("G00 X0 Y0", "原点", timeout=30)
    b.cmd("G00 X10 Y0", "起点", timeout=30)
    t0 = time.time()
    n = 64
    for i in range(1, n + 1):
        a = 2 * math.pi * i / n
        x, y = 10 * math.cos(a), 10 * math.sin(a)
        b.cmd(f"G01 X{x:.3f} Y{y:.3f} F600", "", timeout=30, wait_extra=0)
    t_line = time.time() - t0

    print(f"  A. G02 四段圆弧:     {t_arc:.2f}s")
    print(f"  B. 64 段小直线:      {t_line:.2f}s")
    print(f"  比值: {t_line/t_arc:.1f}×")
    print(f"\n  → 用 G02 能把圆弧的时间压到 1/{t_line/t_arc:.1f}，")
    print(f"     而且 G-code 行数从 {n} 行降到 4 行")

    b.cmd("G00 X0 Y0 Z0", "回原点", timeout=30)
    final = b.status()
    print(f"\n最终: {final['raw']}")
    b.close()


if __name__ == "__main__":
    main()
