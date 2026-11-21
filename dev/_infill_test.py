"""格栅/蜂窝填充的回归测试。

重点覆盖**非对称形状**——历史上的 bug 只在对称形状下才正确，
用正方形和居中圆测试会完全绕过它。
"""

import math

from app.geometry import discretize_source
from app.infill import fill_grid, fill_honeycomb
from app.model import Source

FAILS = []


def check(name, cond, detail=""):
    if cond:
        print(f"[OK  ] {name} {detail}")
    else:
        print(f"[FAIL] {name} {detail}")
        FAILS.append(name)


def split_hv(paths):
    """把路径分成水平线和垂直线两组。"""
    hs, vs = [], []
    for p in paths:
        if len(p) < 2:
            continue
        if abs(p[0][1] - p[-1][1]) < 0.01:
            hs.append(p)
        elif abs(p[0][0] - p[-1][0]) < 0.01:
            vs.append(p)
    return hs, vs


def spans(paths, axis):
    """取一组线的位置（水平线取 y，垂直线取 x）。"""
    idx = 1 if axis == "h" else 0
    return sorted({round(p[0][idx], 1) for p in paths})


def main():
    # ── 非对称矩形：最容易暴露范围写反的 bug ──
    rect_wide = [(0, 0), (80, 0), (80, 20), (0, 20), (0, 0)]
    paths = fill_grid(rect_wide, spacing=10.0, inset=0.0, min_len=1.0, outline=False)
    hs, vs = split_hv(paths)
    hy = spans(hs, "h")
    vx = spans(vs, "v")
    check("宽矩形 水平线位置", hy == [5.0, 15.0], f"y={hy}")
    check("宽矩形 垂直线覆盖全宽", vx == [5.0, 15.0, 25.0, 35.0, 45.0, 55.0, 65.0, 75.0], f"x={vx}")

    # ── 竖长条：反向验证 ──
    rect_tall = [(0, 0), (10, 0), (10, 100), (0, 100), (0, 0)]
    paths = fill_grid(rect_tall, spacing=10.0, inset=0.0, min_len=1.0, outline=False)
    hs, vs = split_hv(paths)
    hy = spans(hs, "h")
    vx = spans(vs, "v")
    check("长条 水平线覆盖全高", len(hy) == 10, f"{len(hy)} 条 y={hy[:3]}…")
    check("长条 垂直线只有一条", vx == [5.0], f"x={vx}")

    # ── 偏心圆：各种中心位置都要对称 ──
    for cx, cy in [(20, 20), (50, 20), (50, 50), (100, 50), (10, 90), (180, 5)]:
        pts = discretize_source(Source(type="circle", cx=cx, cy=cy, r=10, tolerance=0.05))
        ps = fill_grid(pts, spacing=2.0, inset=0.5, min_len=1.5, outline=False)
        hs, vs = split_hv(ps)
        ok = len(hs) == len(vs) and len(hs) > 5
        check(f"圆({cx},{cy}) 横竖对称", ok, f"水平 {len(hs)} 垂直 {len(vs)}")

    # ── 正方形：对称形状（旧测试只覆盖了这一类）──
    square = [(0, 0), (20, 0), (20, 20), (0, 20), (0, 0)]
    paths = fill_grid(square, spacing=4.0, inset=0.0, min_len=1.0, outline=False)
    hs, vs = split_hv(paths)
    check("正方形 横竖数量相等", len(hs) == len(vs) > 0, f"{len(hs)} / {len(vs)}")

    # ── 外轮廓 ──
    paths = fill_grid(square, spacing=4.0, inset=0.5, outline=True)
    check("外轮廓作为第一条", len(paths[0]) >= 4 and paths[0][0] == paths[0][-1],
          f"点数 {len(paths[0])}")

    # ── 错开 ──
    p0 = fill_grid(square, spacing=4.0, inset=0.5, stagger=False, outline=False)
    p1 = fill_grid(square, spacing=4.0, inset=0.5, stagger=True, outline=False)
    _, vs0 = split_hv(p0)
    _, vs1 = split_hv(p1)
    check("错开改变垂直位置", spans(vs0, "v") != spans(vs1, "v"),
          f"{spans(vs0,'v')[:3]} vs {spans(vs1,'v')[:3]}")

    # ── 内缩过大 → 空 ──
    tiny = [(0, 0), (2, 0), (2, 2), (0, 2), (0, 0)]
    paths = fill_grid(tiny, spacing=1.0, inset=5.0, outline=False)
    check("内缩过大返回空", len(paths) == 0, f"{len(paths)} 条")

    # ── 蜂窝 ──
    paths = fill_honeycomb(square, spacing=4.0, inset=0.5, outline=False)
    check("蜂窝有输出", len(paths) > 0, f"{len(paths)} 条")
    all_closed = all(
        len(p) >= 3 and abs(p[0][0] - p[-1][0]) < 0.01 and abs(p[0][1] - p[-1][1]) < 0.01
        for p in paths
    )
    check("蜂窝每条都是闭合环", all_closed, f"{len(paths)} 条")

    # ── 偏移形状（不在原点）──
    off = [(100, 200), (140, 200), (140, 220), (100, 220), (100, 200)]
    paths = fill_grid(off, spacing=10.0, inset=0.0, min_len=1.0, outline=False)
    hs, vs = split_hv(paths)
    xs = [p[0][0] for p in hs]
    ys = [p[0][1] for p in vs]
    check("偏移矩形 水平线在 x 范围内",
          all(100 <= x <= 140 for x in xs), f"x 范围 {min(xs):.0f}..{max(xs):.0f}")
    check("偏移矩形 垂直线在 y 范围内",
          all(200 <= y <= 220 for y in ys), f"y 范围 {min(ys):.0f}..{max(ys):.0f}")

    print()
    if FAILS:
        print(f"{len(FAILS)} 项失败: {FAILS}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
