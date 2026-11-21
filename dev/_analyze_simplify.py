"""量化轮廓简化能省多少指令。

切片出来的多边形常常含大量共线点（同一条直线上被切成很多小段）。
删掉这些中间点，几何完全不变，但移动指令会少很多。
而板子每条指令有 200~360ms 固定开销，所以这是最大的提速杠杆。
"""

from __future__ import annotations

import math
import sys

sys.path.insert(0, ".")

from app.mesh import load_mesh, slice_layers

STL = r"B:\Users\Echo\.dsh\attachments\v1\files\c0\c0a2a50a67ea18fd315f5dfeeab7d67ed9ab3fb017702a9f83cef51bd1d0905d\05-格栅立方.stl"


def simplify(pts: list[tuple[float, float]], tol: float = 0.02) -> list[tuple[float, float]]:
    """删掉共线的中间点。tol 是点到直线的容差（mm）。"""
    n = len(pts)
    if n <= 3:
        return pts
    closed = (abs(pts[0][0] - pts[-1][0]) < 1e-9 and abs(pts[0][1] - pts[-1][1]) < 1e-9)
    core = pts[:-1] if closed else pts
    m = len(core)
    if m <= 2:
        return pts

    keep = []
    for i in range(m):
        a = core[(i - 1) % m]
        b = core[i]
        c = core[(i + 1) % m]
        # b 到 a-c 直线的距离
        ax, ay = a
        bx, by = b
        cx, cy = c
        dx, dy = cx - ax, cy - ay
        L = math.hypot(dx, dy)
        if L < 1e-12:
            d = math.hypot(bx - ax, by - ay)
        else:
            d = abs((bx - ax) * dy - (by - ay) * dx) / L
        if d > tol:
            keep.append(b)
    if len(keep) < 3:
        return pts
    if closed:
        keep.append(keep[0])
    return keep


def main() -> None:
    with open(STL, "rb") as f:
        data = f.read()
    tris, _ = load_mesh("05-格栅立方.stl", data)
    res = slice_layers(tris, layer_height=0.4, max_layers=400)

    raw_pts = 0
    sim_pts = 0
    raw_loops = 0

    print(f"{'层':>3} {'轮廓数':>6} {'原始点':>8} {'简化后':>8} {'压缩':>6}")
    print("-" * 42)
    for i, (z, loops) in enumerate(res.layers):
        r = sum(len(p) for p in loops)
        s = sum(len(simplify(p)) for p in loops)
        raw_pts += r
        sim_pts += s
        raw_loops += len(loops)
        if i < 6:
            print(f"{i+1:3d} {len(loops):6d} {r:8d} {s:8d} {r/max(s,1):5.1f}x")
    print("  ...")
    print(f"{'合计':>3} {raw_loops:6d} {raw_pts:8d} {sim_pts:8d} {raw_pts/max(sim_pts,1):5.1f}x")

    print()
    print(f"总点数: {raw_pts} → {sim_pts}  （省 {raw_pts - sim_pts} 个移动指令）")
    print(f"按 0.35 秒/指令算，省下 {(raw_pts - sim_pts) * 0.35 / 60:.0f} 分钟")

    # 抽查一个轮廓看看形状
    if res.layers:
        _, loops = res.layers[0]
        if loops:
            lp = loops[0]
            print()
            print(f"第一个轮廓：{len(lp)} 点 → 简化后 {len(simplify(lp))} 点")
            for p in lp[:14]:
                print(f"   ({p[0]:7.2f}, {p[1]:7.2f})")


if __name__ == "__main__":
    main()
