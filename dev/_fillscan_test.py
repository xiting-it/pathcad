"""扫描线填充（移植版）的测试：与现有的 Shapely 实现对比。"""

import math

from app.fill_scan import fill_grid_cpp, fill_scan
from app.infill import fill_grid
from app.geometry import discretize_source
from app.model import Source

FAILS = []


def check(name, cond, detail=""):
    if cond:
        print(f"[OK  ] {name} {detail}")
    else:
        print(f"[FAIL] {name} {detail}")
        FAILS.append(name)


def seg_len(s):
    return math.dist(s[0], s[1])


def total_len(segs):
    return sum(seg_len(s) for s in segs)


def bbox(segs):
    xs = [p[0] for s in segs for p in s]
    ys = [p[1] for s in segs for p in s]
    if not xs:
        return None
    return (min(xs), min(ys), max(xs), max(ys))


def main():
    # ── 正方形 ──
    sq = [[(0, 0), (20, 0), (20, 20), (0, 20)]]
    segs = fill_scan(sq, spacing=2.0)
    # 原算法从 ymax-spacing 起扫，最后一条恰好落在 ymin 上（半开区间判定为无交点）被丢弃
    check("正方形 水平扫描", len(segs) == 10, f"{len(segs)} 条")
    check("正方形 线段都水平",
          all(abs(s[0][1] - s[1][1]) < 1e-9 for s in segs))
    check("正方形 长度正确",
          all(abs(seg_len(s) - 20) < 1e-6 for s in segs),
          f"首条 {seg_len(segs[0]):.2f}mm")

    # ── 非对称矩形（旧 bug 的照妖镜）──
    wide = [[(0, 0), (80, 0), (80, 20), (0, 20)]]
    hs = fill_scan(wide, spacing=10.0, angle_deg=0)
    vs = fill_scan(wide, spacing=10.0, angle_deg=90)
    check("宽矩形 水平线数量", len(hs) == 2, f"{len(hs)} 条")
    check("宽矩形 水平线长度 80", all(abs(seg_len(s) - 80) < 1e-6 for s in hs))
    check("宽矩形 垂直线数量", len(vs) == 8, f"{len(vs)} 条")
    check("宽矩形 垂直线长度 20", all(abs(seg_len(s) - 20) < 1e-6 for s in vs))

    # ── 竖长条 ──
    tall = [[(0, 0), (10, 0), (10, 100), (0, 100)]]
    hs2 = fill_scan(tall, spacing=10.0, angle_deg=0)
    vs2 = fill_scan(tall, spacing=10.0, angle_deg=90)
    check("长条 水平线 10 条", len(hs2) == 10, f"{len(hs2)} 条")
    check("长条 垂直线 1 条", len(vs2) == 1, f"{len(vs2)} 条")

    # ── 圆（不同中心）──
    for cx, cy in [(0, 0), (50, 20), (100, 100), (-30, 70)]:
        pts = discretize_source(Source(type="circle", cx=cx, cy=cy, r=10, tolerance=0.05))
        h = fill_scan([pts], spacing=2.0)
        v = fill_scan([pts], spacing=2.0, angle_deg=90)
        # 圆的弦长中位数应该接近 2*sqrt(r^2-(r-spacing/2)^2) ≈ 6.2
        check(f"圆({cx},{cy}) 横竖数量接近",
              abs(len(h) - len(v)) <= 1, f"水平 {len(h)} 垂直 {len(v)}")
        if h:
            mid = sorted(seg_len(s) for s in h)[len(h) // 2]
            # r=10、间距2：最长的弦约 19.9mm，中位数应在 15~20 之间
            check(f"圆({cx},{cy}) 中线长度合理", 12.0 < mid < 20.0, f"{mid:.2f}mm")

    # ── 45 度 ──
    segs45 = fill_scan(sq, spacing=2.0, angle_deg=45)
    check("45度 有输出", len(segs45) > 0, f"{len(segs45)} 条")
    if segs45:
        ang = math.degrees(math.atan2(
            segs45[0][1][1] - segs45[0][0][1],
            segs45[0][1][0] - segs45[0][0][0]))
        check("45度 线段角度正确", abs(ang - 45) < 0.5, f"{ang:.2f}°")

    # ── 带孔洞（环形）──
    outer = [(0, 0), (40, 0), (40, 40), (0, 40)]
    hole = [(15, 15), (25, 15), (25, 25), (15, 25)]
    ring = fill_scan([outer, hole], spacing=4.0)
    # 穿过孔洞的那几条会被切成左右两段（0->15 和 25->40）
    through = [s for s in ring if abs((s[1][0] - s[0][0]) - 15.0) < 0.01]
    check("环形 孔洞处被切开", len(through) == 4, f"被切开的段 {len(through)} 条")
    left = [s for s in through if abs(s[0][0]) < 0.01]
    right = [s for s in through if abs(s[1][0] - 40) < 0.01]
    check("环形 切点在孔洞边界",
          len(left) == 2 and len(right) == 2
          and all(abs(s[1][0] - 15) < 0.01 for s in left)
          and all(abs(s[0][0] - 25) < 0.01 for s in right),
          f"左 {len(left)} 段止于 15，右 {len(right)} 段起于 25")
    check("环形 段数多于实心", len(ring) > 10, f"共 {len(ring)} 段")

    # ── 多次调用不污染（原实现的 m_vEdgeRect bug）──
    a = fill_scan(sq, spacing=2.0)
    b = fill_scan(sq, spacing=2.0)
    check("重复调用结果一致", len(a) == len(b), f"{len(a)} vs {len(b)}")
    c = fill_scan([[(-100, -100), (-60, -100), (-60, -60), (-100, -60)]], spacing=2.0)
    d = fill_scan(sq, spacing=2.0)
    check("换形状后不残留", len(c) == 20 and len(d) == len(a),
          f"远处 {len(c)}, 原形状 {len(d)}")

    # ── 与 Shapely 实现对比 ──
    print()
    print("── 与现有 Shapely 实现对比（20x20, 间距2）──")
    cpp_h = fill_scan(sq, spacing=2.0)
    shapely_paths = fill_grid([(0, 0), (20, 0), (20, 20), (0, 20), (0, 0)],
                              spacing=2.0, inset=0.0, min_len=0.0, outline=False)
    shapely_h = [p for p in shapely_paths if abs(p[0][1] - p[-1][1]) < 0.01]
    print(f"  移植版 水平: {len(cpp_h)} 条, 总长 {total_len(cpp_h):.1f}mm")
    print(f"  Shapely 水平: {len(shapely_h)} 条, 总长 {sum(math.dist(p[0],p[-1]) for p in shapely_h):.1f}mm")
    check("两条实现水平线数量一致", len(cpp_h) == len(shapely_h),
          f"{len(cpp_h)} vs {len(shapely_h)}")

    # 宽矩形对比
    cpp_w = fill_scan(wide, spacing=10.0)
    sh_w = fill_grid([(0, 0), (80, 0), (80, 20), (0, 20), (0, 0)],
                     spacing=10.0, inset=0.0, min_len=0.0, outline=False)
    sh_w_h = [p for p in sh_w if abs(p[0][1] - p[-1][1]) < 0.01]
    check("宽矩形两实现一致", len(cpp_w) == len(sh_w_h),
          f"{len(cpp_w)} vs {len(sh_w_h)}")

    # ── 格栅（两方向）──
    g = fill_grid_cpp(sq, spacing=4.0)
    check("格栅两方向", len(g) == 10, f"{len(g)} 段")

    # ── 退化输入 ──
    check("空输入", fill_scan([], 2.0) == [])
    check("间距为0", fill_scan(sq, 0) == [])
    check("间距过大", fill_scan(sq, 100.0) == [])
    check("单点轮廓", fill_scan([[(0, 0)]], 2.0) == [])

    print()
    if FAILS:
        print(f"{len(FAILS)} 项失败: {FAILS}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
