"""格栅填充（grid / lattice infill）。

和"扫描线填满"不同，格栅只在内部织一组互相垂直的筋，
中间留空——这就是视频里那台机器打的薄壁多孔结构。

核心扫描线算法移植自激光切割的 CIMFillAlgorithm（见 app/fill_scan.py），
比原来的 Shapely 版本更可控：边界处理、孔洞奇偶规则、零长段消解都是显式的。

关键约束（DIW 场景）：
- 每条筋是**独立笔画**，各自开场关阀。绝不能连成蛇形，
  否则连接段会带着阀门开着走，在空白处挤出材料。
- 外形轮廓单独一条，不参与格栅。
"""

from __future__ import annotations

import math

from shapely.geometry import Polygon
from shapely.ops import unary_union

from .fill_scan import fill_scan

from .geometry import _polygon_of


def _contours_of(poly) -> list[list[tuple[float, float]]]:
    """把 Shapely 多边形（含内洞）拆成轮廓点串列表。

    内洞单独作为一条闭合串传进去，扫描线算法用奇偶规则处理孔洞。
    """
    out: list[list[tuple[float, float]]] = []
    geoms = [poly] if poly.geom_type == "Polygon" else list(getattr(poly, "geoms", []))
    for g in geoms:
        if g.geom_type != "Polygon":
            continue
        out.append([(c[0], c[1]) for c in g.exterior.coords])
        for r in g.interiors:
            out.append([(c[0], c[1]) for c in r.coords])
    return out


def fill_grid(
    pts: list[tuple[float, float]],
    spacing: float = 2.0,
    inset: float = 0.4,
    min_len: float = 1.5,
    stagger: bool = False,
    outline: bool = True,
) -> list[list[tuple[float, float]]]:
    """格栅填充。

    参数：
      spacing  格栅间距（两个方向上都是），含义是「孔的大小」不是线宽
      inset    格栅离外轮廓的距离（外框由 outline 单独生成）
      min_len  太短的筋丢弃（无 look-ahead 时短段跑不满速度）
      stagger  两条方向的筋错开半个间距，孔洞更均匀
      outline  是否附带外轮廓（作为第一条路径）

    返回若干条独立路径（每条都是一个独立笔画）。
    """
    poly = _polygon_of(pts)
    if poly is None:
        return []

    result: list[list[tuple[float, float]]] = []

    # 1. 外轮廓
    if outline:
        outer = [(c[0], c[1]) for c in poly.exterior.coords]
        if len(outer) >= 3:
            if outer[0] != outer[-1]:
                outer.append(outer[0])
            result.append(outer)

    # 2. 内缩后的区域用来织格栅
    inner = poly.buffer(-inset) if inset > 0 else poly
    if inner.is_empty:
        return result

    contours = _contours_of(inner)
    if not contours:
        return result

    # 3. 两个方向各扫一遍，得到互相垂直的筋
    off = spacing / 2 if stagger else 0.0
    hs = fill_scan(contours, spacing, angle_deg=0.0, offset=0.0, min_len=min_len)
    vs = fill_scan(contours, spacing, angle_deg=90.0, offset=off, min_len=min_len)

    # 4. 每条线段单独成为一条笔画（绝不合并成蛇形）
    for a, b in hs + vs:
        result.append([a, b])

    return result


def fill_honeycomb(
    pts: list[tuple[float, float]],
    spacing: float = 2.0,
    inset: float = 0.4,
    min_len: float = 1.5,
    outline: bool = True,
) -> list[list[tuple[float, float]]]:
    """蜂窝（六边形）格栅。

    实心六边形阵列 → 取边界 → 得到互相咬合的蜂窝壁。
    比方格更像生物支架，但每条壁都很短，对慢速打印不友好。
    """
    poly = _polygon_of(pts)
    if poly is None:
        return []

    result: list[list[tuple[float, float]]] = []
    if outline:
        outer = [(c[0], c[1]) for c in poly.exterior.coords]
        if len(outer) >= 3:
            result.append(outer)

    inner = poly.buffer(-inset) if inset > 0 else poly
    if inner.is_empty:
        return result

    R = spacing / math.sqrt(3)  # 六边形外接圆半径
    minx, miny, maxx, maxy = inner.bounds

    hexes = []
    row = 0
    y = miny
    while y <= maxy + R and len(hexes) < 20000:
        x = minx + (R * math.sqrt(3) / 2 if row % 2 else 0)
        while x <= maxx + R and len(hexes) < 20000:
            coords = [
                (x + R * math.cos(math.pi / 3 * i + math.pi / 6),
                 y + R * math.sin(math.pi / 3 * i + math.pi / 6))
                for i in range(6)
            ]
            hexes.append(Polygon(coords))
            x += R * math.sqrt(3)
        y += R * 1.5
        row += 1

    if not hexes:
        return result

    merged = unary_union(hexes).intersection(inner)
    if merged.is_empty:
        return result

    geoms = [merged] if merged.geom_type == "Polygon" else list(getattr(merged, "geoms", []))
    for g in geoms:
        if g.geom_type != "Polygon":
            continue
        # 外环 + 所有内洞
        rings = [list(g.exterior.coords)] + [list(r.coords) for r in g.interiors]
        for r in rings:
            if len(r) < 3:
                continue
            if r[0] != r[-1]:
                r.append(r[0])
            length = sum(math.dist(r[i], r[i + 1]) for i in range(len(r) - 1))
            if length >= min_len:
                result.append([(p[0], p[1]) for p in r])

    return result
