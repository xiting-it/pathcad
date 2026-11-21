"""扫描线填充（移植自激光切割的 CIMFillAlgorithm::FillSide）。

原实现是 C++ / MFC 风格，用 long + dScale=10000 做定点运算。
这里改用 float，精度更高、代码更短。

核心流程（与原实现一一对应）：
  1. 按角度反向旋转，让填充方向变成水平
  2. 逐条轮廓建边表（(ymin, ymax, x@ymax, dx)），跳过水平边
  3. 边表按 ymax 降序
  4. 从 ymax-spacing 起，每条扫描线求所有交点 x
  5. 交点按 x 排序 → 若个数为奇数则跳过（数据异常）
  6. 去掉相邻 x 相等的零长段
  7. 两两配对成独立线段
  8. 旋转回去

关键差异（相对原实现）：
  - **每次调用重建边表**。原实现的 m_vEdgeRect 是成员变量且从不 clear，
    连续调用会把上一次的边带进来（原实现的一个真 bug）。
  - **不做增量删除优化**。原实现在 FillScanIntersectionPoints 末尾
    按 ymin 增量删边，但边表是按 ymax 排序的，两个顺序不一致会漏删。
    这里改成每条扫描线重新判断，慢一点但不会错。
  - 输出**独立线段**（原实现靠 MOVE_TO/LINE_TO 交替表达同样语义）。
"""

from __future__ import annotations

import math

Point = tuple[float, float]
Segment = tuple[Point, Point]


def _norm_contours(contours: list[list[Point]]) -> list[list[Point]]:
    """统一成闭合点串（首尾不重复），丢掉点数不足的。"""
    out: list[list[Point]] = []
    for c in contours:
        pts = [(float(x), float(y)) for x, y in c]
        if len(pts) >= 2 and pts[0] == pts[-1]:
            pts = pts[:-1]
        if len(pts) >= 3:
            out.append(pts)
    return out


def _rotate(pts: list[Point], ang_rad: float, cx: float, cy: float) -> list[Point]:
    if abs(ang_rad) < 1e-12:
        return pts
    ca, sa = math.cos(ang_rad), math.sin(ang_rad)
    return [
        ((x - cx) * ca - (y - cy) * sa + cx,
         (x - cx) * sa + (y - cy) * ca + cy)
        for x, y in pts
    ]


def _build_edges(contours: list[list[Point]]) -> list[tuple[float, float, float, float]]:
    """建边表。每条边存 (ymin, ymax, x_at_ymax, dx)。

    dx = (x1-x0)/(y1-y0)，用于插值。水平边（y 相同）无法与水平扫描线相交，跳过。
    """
    edges: list[tuple[float, float, float, float]] = []
    for c in contours:
        n = len(c)
        for i in range(n):
            x0, y0 = c[i]
            x1, y1 = c[(i + 1) % n]
            if abs(y1 - y0) < 1e-12:
                continue  # 水平边
            dx = (x1 - x0) / (y1 - y0)
            if y1 > y0:
                edges.append((y0, y1, x1, dx))
            else:
                edges.append((y1, y0, x0, dx))
    # 按 ymax 降序（对应 SortEdge）
    edges.sort(key=lambda e: e[1], reverse=True)
    return edges


def fill_scan(
    contours: list[list[Point]],
    spacing: float,
    angle_deg: float = 0.0,
    offset: float = 0.0,
    min_len: float = 0.0,
) -> list[Segment]:
    """在闭合轮廓内生成一组平行扫描线，返回独立线段。

    contours  若干闭合轮廓（允许嵌套，用奇偶规则处理孔洞）
    spacing   扫描线间距（mm）
    angle_deg 填充方向，0 = 水平线
    offset    第一条线相对起点的偏移（用于错开两遍填充）
    min_len   丢弃太短的线段（无 look-ahead 时短段跑不满速度）
    """
    contours = _norm_contours(contours)
    if not contours or spacing <= 0:
        return []

    # 包围盒，作为旋转中心
    xs = [p[0] for c in contours for p in c]
    ys = [p[1] for c in contours for p in c]
    cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2

    ang = math.radians(-angle_deg)  # 先把方向转成水平
    rot = [_rotate(c, ang, cx, cy) for c in contours]

    ymin = min(p[1] for c in rot for p in c)
    ymax = max(p[1] for c in rot for p in c)
    height = ymax - ymin
    if height <= 1e-9:
        return []

    # 边的构建必须用旋转后的轮廓
    edges = _build_edges(rot)
    if not edges:
        return []

    # 扫描线位置：从 ymax - spacing/2 起，首尾各留半格，分布更均匀。
    # 原实现从 ymax - spacing 起扫，最后一条会落在 ymin 上（半开区间判定为
    # 无交点）被丢掉，结果是两端各空近一整格。
    n_lines = int(height / spacing)
    if n_lines <= 0:
        return []
    first = ymax - spacing / 2 - offset

    out_rot: list[Segment] = []
    guard = 0
    max_lines = 20000
    y = first
    while y > ymin + 1e-9 and guard < max_lines:
        guard += 1
        y_now = y
        y -= spacing

        # 求这条线与所有边的交点（半开区间 ymin < y <= ymax，避免顶点重复计数）
        hits: list[float] = []
        for e in edges:
            e_ymin, e_ymax, e_xmax, e_dx = e
            if e_ymax < y_now:
                # 边表按 ymax 降序，后面的边 ymax 更小，可以提前结束
                break
            if e_ymin < y_now <= e_ymax:
                hits.append(e_xmax + e_dx * (y_now - e_ymax))

        if len(hits) < 2 or len(hits) % 2 != 0:
            continue  # 交点个数为奇数说明数据异常，跳过（对应原实现的 return）

        hits.sort()

        # 去掉相邻 x 相等的零长段（对应原实现的 erase 逻辑）
        clean: list[float] = []
        i = 0
        while i < len(hits):
            if i + 1 < len(hits) and abs(hits[i] - hits[i + 1]) < 1e-9:
                i += 2  # 成对消掉
            else:
                clean.append(hits[i])
                i += 1

        if len(clean) < 2 or len(clean) % 2 != 0:
            continue

        # 两两配对成线段（对应 MOVE_TO/LINE_TO 交替）
        for k in range(0, len(clean) - 1, 2):
            x0, x1 = clean[k], clean[k + 1]
            if x1 - x0 <= 1e-9:
                continue
            out_rot.append(((x0, y_now), (x1, y_now)))

    if not out_rot:
        return []

    # 过滤太短的
    if min_len > 0:
        out_rot = [s for s in out_rot if (s[1][0] - s[0][0]) >= min_len]
    if not out_rot:
        return []

    # 旋转回去
    if abs(ang) > 1e-12:
        back: list[Segment] = []
        for a, b in out_rot:
            pa = _rotate([a], -ang, cx, cy)[0]
            pb = _rotate([b], -ang, cx, cy)[0]
            back.append((pa, pb))
        return back
    return out_rot


def fill_grid_cpp(
    contours: list[list[Point]],
    spacing: float,
    offset: float = 0.0,
) -> list[Segment]:
    """两个垂直方向各扫一遍，得到格栅。"""
    hs = fill_scan(contours, spacing, 0.0, offset)
    vs = fill_scan(contours, spacing, 90.0, offset)
    return hs + vs
