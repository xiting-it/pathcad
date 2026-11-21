"""几何引擎：图元离散、填充、排序。

全部输出「笔画点序列」（list[list[tuple[float,float]]]），不关心 IO。
"""

from __future__ import annotations

import math

from shapely.geometry import LinearRing, LineString, MultiPolygon, Polygon, box
from shapely.ops import unary_union

from .model import Point, Source

# ── 离散 ────────────────────────────────────────────────


def discretize_circle(cx: float, cy: float, r: float, tol: float = 0.05) -> list[tuple[float, float]]:
    """按弦高误差离散圆。tol 是弦到弧的最大偏差(mm)。"""
    if r <= 0:
        return []
    # 弦高 h = r(1-cos(θ/2)) => θ = 2*acos(1 - h/r)
    ratio = max(-1.0, min(1.0, 1.0 - tol / r))
    theta = 2.0 * math.acos(ratio)
    n = max(8, min(2048, int(math.ceil(2 * math.pi / theta))))
    return [
        (cx + r * math.cos(2 * math.pi * i / n), cy + r * math.sin(2 * math.pi * i / n))
        for i in range(n)
    ]


def discretize_bezier(
    p0: tuple[float, float],
    ctrl: list[tuple[float, float]],
    p1: tuple[float, float],
    tol: float = 0.05,
) -> list[tuple[float, float]]:
    """自适应 de Casteljau 离散三次贝塞尔。"""
    if not ctrl:
        return [p0, p1]
    if len(ctrl) == 1:
        ctrl = [ctrl[0], ctrl[0]]
    c1, c2 = ctrl[0], ctrl[1]

    def flat_enough(a, b, c, d) -> bool:
        # 控制点到弦的最大距离
        dx, dy = d[0] - a[0], d[1] - a[1]
        norm = math.hypot(dx, dy)
        if norm < 1e-9:
            return True
        d1 = abs((b[0] - a[0]) * dy - (b[1] - a[1]) * dx) / norm
        d2 = abs((c[0] - a[0]) * dy - (c[1] - a[1]) * dx) / norm
        return max(d1, d2) <= tol

    out = [p0]

    def rec(a, b, c, d, depth=0):
        if depth > 16 or flat_enough(a, b, c, d):
            out.append(d)
            return
        ab = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
        bc = ((b[0] + c[0]) / 2, (b[1] + c[1]) / 2)
        cd = ((c[0] + d[0]) / 2, (c[1] + d[1]) / 2)
        abc = ((ab[0] + bc[0]) / 2, (ab[1] + bc[1]) / 2)
        bcd = ((bc[0] + cd[0]) / 2, (bc[1] + cd[1]) / 2)
        mid = ((abc[0] + bcd[0]) / 2, (abc[1] + bcd[1]) / 2)
        rec(a, ab, abc, mid, depth + 1)
        rec(mid, bcd, cd, d, depth + 1)

    rec(p0, c1, c2, p1)
    return out


def _apply_rotation(pts: list[tuple[float, float]], src: Source) -> list[tuple[float, float]]:
    """按 src.rotation 绕中心旋转点序列。没设角度就原样返回。"""
    ang = src.rotation or 0.0
    if abs(ang) < 1e-9 or not pts:
        return pts
    # 中心：优先用显式给的，否则用包围盒中心
    if src.rot_cx is not None and src.rot_cy is not None:
        cx, cy = src.rot_cx, src.rot_cy
    else:
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
    a = math.radians(ang)
    ca, sa = math.cos(a), math.sin(a)
    return [
        ((x - cx) * ca - (y - cy) * sa + cx,
         (x - cx) * sa + (y - cy) * ca + cy)
        for x, y in pts
    ]


def discretize_source(src: Source) -> list[tuple[float, float]]:
    """把一个图元变成点序列。闭合图元首尾点重复。

    所有类型都支持 src.rotation（绕中心旋转，度）。
    """
    return _apply_rotation(_discretize_base(src), src)


def _discretize_base(src: Source) -> list[tuple[float, float]]:
    tol = src.tolerance or 0.05
    if src.type == "rect":
        x, y = src.x or 0.0, src.y or 0.0
        w, h = src.w or 0.0, src.h or 0.0
        return [(x, y), (x + w, y), (x + w, y + h), (x, y + h), (x, y)]
    if src.type == "circle":
        pts = discretize_circle(src.cx or 0.0, src.cy or 0.0, src.r or 0.0, tol)
        return pts + [pts[0]] if pts else []
    if src.type == "ellipse":
        cx, cy = src.cx or 0.0, src.cy or 0.0
        rx, ry = src.rx or 0.0, src.ry or 0.0
        if rx <= 0 or ry <= 0:
            return []
        n = max(36, min(720, int(2 * math.pi * max(rx, ry) / max(tol, 1e-3))))
        pts = [
            (cx + rx * math.cos(2 * math.pi * i / n), cy + ry * math.sin(2 * math.pi * i / n))
            for i in range(n)
        ]
        return pts + [pts[0]]
    if src.type == "polygon":
        cx, cy = src.cx or 0.0, src.cy or 0.0
        r = src.r or 0.0
        sides = max(3, int(src.sides or 6))
        if r <= 0:
            return []
        pts = [
            (cx + r * math.cos(-math.pi / 2 + 2 * math.pi * i / sides),
             cy + r * math.sin(-math.pi / 2 + 2 * math.pi * i / sides))
            for i in range(sides)
        ]
        return pts + [pts[0]]
    if src.type == "diamond":
        cx, cy = src.cx or 0.0, src.cy or 0.0
        rx, ry = src.rx or 0.0, src.ry or 0.0
        if rx <= 0 or ry <= 0:
            return []
        return [(cx, cy - ry), (cx + rx, cy), (cx, cy + ry), (cx - rx, cy), (cx, cy - ry)]
    if src.type == "star":
        cx, cy = src.cx or 0.0, src.cy or 0.0
        r = src.r or 0.0
        inner = src.inner or (r * 0.45)
        points = max(3, int(src.points or 5))
        if r <= 0:
            return []
        pts = []
        for i in range(points * 2):
            a = -math.pi / 2 + math.pi * i / points
            rad = r if i % 2 == 0 else inner
            pts.append((cx + rad * math.cos(a), cy + rad * math.sin(a)))
        return pts + [pts[0]]
    if src.type in ("polyline", "svg"):
        pts = [(p.x, p.y) for p in (src.pts or [])]
        return pts
    if src.type == "bezier":
        pts = src.pts or []
        if len(pts) < 2:
            return []
        p0 = (pts[0].x, pts[0].y)
        p1 = (pts[-1].x, pts[-1].y)
        ctrl = [(p.x, p.y) for p in pts[1:-1]]
        return discretize_bezier(p0, ctrl, p1, tol)
    return []


# ── 填充 ────────────────────────────────────────────────


def _polygon_of(pts: list[tuple[float, float]]) -> Polygon | None:
    if len(pts) < 3:
        return None
    ring = pts
    if ring[0] == ring[-1]:
        ring = ring[:-1]
    if len(ring) < 3:
        return None
    try:
        poly = Polygon(ring)
    except Exception:
        return None
    if not poly.is_valid:
        poly = poly.buffer(0)
    return poly if not poly.is_empty else None


def fill_scanline(
    pts: list[tuple[float, float]],
    spacing: float = 1.0,
    angle_deg: float = 0.0,
    inset: float = 0.0,
    min_len: float = 1.5,
) -> list[list[tuple[float, float]]]:
    """平行线扫描填充。

    用 Shapely 在旋转坐标系里切，再转回来——比手写扫描线稳。
    min_len 用来过滤太短的线段（无 look-ahead 时短段跑不满速度）。
    """
    poly = _polygon_of(pts)
    if poly is None:
        return []
    if inset > 0:
        poly = poly.buffer(-inset)
        if poly.is_empty:
            return []

    a = math.radians(angle_deg)
    ca, sa = math.cos(-a), math.sin(-a)

    def rot(p):
        return (p[0] * ca - p[1] * sa, p[0] * sa + p[1] * ca)

    def unrot(p):
        return (p[0] * ca + p[1] * sa, -p[0] * sa + p[1] * ca)

    coords = [rot(c) for c in poly.exterior.coords]
    rp = Polygon(coords) if poly.geom_type == "Polygon" else unary_union(
        [Polygon([rot(c) for c in g.exterior.coords]) for g in getattr(poly, "geoms", [])]
    )

    minx, miny, maxx, maxy = rp.bounds
    lines: list[list[tuple[float, float]]] = []
    y = miny + spacing / 2
    flip = False
    while y <= maxy:
        seg = LineString([(minx - 1, y), (maxx + 1, y)]).intersection(rp)
        if not seg.is_empty:
            parts = [seg] if seg.geom_type == "LineString" else list(getattr(seg, "geoms", []))
            row: list[list[tuple[float, float]]] = []
            for p in parts:
                if p.geom_type != "LineString" or p.length < min_len:
                    continue
                cs = [unrot(c) for c in p.coords]
                row.append(cs)
            if flip:
                row.reverse()
                row = [list(reversed(r)) for r in row]
            lines.extend(row)
            flip = not flip
        y += spacing
    return lines


def fill_concentric(
    pts: list[tuple[float, float]], step: float = 1.0, min_len: float = 1.5
) -> list[list[tuple[float, float]]]:
    """同心轮廓填充（由外向内缩）。"""
    poly = _polygon_of(pts)
    if poly is None:
        return []
    out: list[list[tuple[float, float]]] = []
    k = 1
    while True:
        inner = poly.buffer(-step * k)
        if inner.is_empty:
            break
        geoms = [inner] if inner.geom_type == "Polygon" else list(getattr(inner, "geoms", []))
        for g in geoms:
            if g.geom_type != "Polygon":
                continue
            cs = list(g.exterior.coords)
            if len(cs) >= 2:
                out.append(cs)
        k += 1
        if k > 500:
            break
    return out


# ── 排序 ────────────────────────────────────────────────


def _dist(a, b) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def sort_paths(
    paths: list[list[tuple[float, float]]],
    origin: tuple[float, float] = (0.0, 0.0),
    allow_reverse: bool = True,
) -> list[list[tuple[float, float]]]:
    """最近邻贪心排序，减少空驶。可选允许反向（闭合路径无所谓，开放路径有用）。"""
    if not paths:
        return []
    remaining = list(range(len(paths)))
    out: list[list[tuple[float, float]]] = []
    cur = origin
    while remaining:
        best_i = -1
        best_d = float("inf")
        best_rev = False
        for i in remaining:
            p = paths[i]
            d0 = _dist(cur, p[0])
            if d0 < best_d:
                best_i, best_d, best_rev = i, d0, False
            if allow_reverse:
                d1 = _dist(cur, p[-1])
                if d1 < best_d:
                    best_i, best_d, best_rev = i, d1, True
        p = paths[best_i]
        if best_rev:
            p = list(reversed(p))
        out.append(p)
        cur = p[-1]
        remaining.remove(best_i)
    return out


def merge_paths(paths: list[list[tuple[float, float]]], tol: float = 0.05) -> list[list[tuple[float, float]]]:
    """合并首尾相接的路径（减少空驶与抬针次数）。

    ⚠️ DIW 场景慎用：合并后连接两段路径的那条直线会带着阀门开着走，
    会真的挤出材料。只在「路径本来就是连续轨迹」时才合并。
    默认走 fill 接口时 merge=False。
    """
    if not paths:
        return []
    merged: list[list[tuple[float, float]]] = []
    for p in paths:
        if not p:
            continue
        if merged and _dist(merged[-1][-1], p[0]) <= tol:
            merged[-1] = merged[-1] + p[1:]
        else:
            merged.append(list(p))
    return merged


# ── 工具 ────────────────────────────────────────────────


def to_points(cs: list[tuple[float, float]]) -> list[Point]:
    return [Point(x=round(x, 4), y=round(y, 4)) for x, y in cs]


def path_length(pts: list[tuple[float, float]]) -> float:
    return sum(_dist(pts[i], pts[i + 1]) for i in range(len(pts) - 1))


def bbox_of(paths: list[list[tuple[float, float]]]) -> tuple[float, float, float, float]:
    xs = [p[0] for path in paths for p in path]
    ys = [p[1] for path in paths for p in path]
    if not xs:
        return (0.0, 0.0, 0.0, 0.0)
    return (min(xs), min(ys), max(xs), max(ys))
