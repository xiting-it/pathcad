"""SVG 导入：把 SVG 里的路径解析成点序列。

只依赖标准库。支持：
  - <path d="..."> 的 M/L/H/V/C/S/Q/T/A/Z（绝对+相对）
  - <rect> <circle> <ellipse> <line> <polyline> <polygon>
  - 简单的 transform: translate / scale / matrix
  - viewBox 缩放：统一换算到 mm

不支持的（明确报错或忽略）：文本 <text>、渐变、滤镜、clipPath、嵌套 symbol/use。
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from xml.etree import ElementTree as ET

# ── 数值与命令解析 ───────────────────────────────────────

NUM_RE = re.compile(r"[-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?")
CMD_RE = re.compile(r"([MmZzLlHhVvCcSsQqTtAa])")


def _nums(s: str) -> list[float]:
    return [float(m.group()) for m in NUM_RE.finditer(s)]


def _tokenize_path(d: str):
    """把 path 的 d 拆成 [(cmd, [nums...]), ...]。"""
    out = []
    pos = 0
    cur_cmd = None
    for m in CMD_RE.finditer(d):
        if cur_cmd is not None:
            args = _nums(d[pos:m.start()])
            out.append((cur_cmd, args))
        cur_cmd = m.group(1)
        pos = m.end()
    if cur_cmd is not None:
        out.append((cur_cmd, _nums(d[pos:])))
    return out


# ── 变换矩阵 ─────────────────────────────────────────────

Mat = tuple[float, float, float, float, float, float]  # a b c d e f


def mat_mul(m1: Mat, m2: Mat) -> Mat:
    a1, b1, c1, d1, e1, f1 = m1
    a2, b2, c2, d2, e2, f2 = m2
    return (
        a1 * a2 + c1 * b2,
        b1 * a2 + d1 * b2,
        a1 * c2 + c1 * d2,
        b1 * c2 + d1 * d2,
        a1 * e2 + c1 * f2 + e1,
        b1 * e2 + d1 * f2 + f1,
    )


def mat_apply(m: Mat, x: float, y: float) -> tuple[float, float]:
    a, b, c, d, e, f = m
    return (a * x + c * y + e, b * x + d * y + f)


IDENTITY: Mat = (1, 0, 0, 1, 0, 0)


def parse_transform(s: str) -> Mat:
    """解析 transform 属性，支持 translate/scale/matrix/rotate。"""
    if not s:
        return IDENTITY
    m = IDENTITY
    for name, args in re.findall(r"(\w+)\s*\(([^)]*)\)", s):
        v = _nums(args)
        if name == "translate":
            t = (1, 0, 0, 1, v[0] if v else 0, v[1] if len(v) > 1 else 0)
        elif name == "scale":
            sx = v[0] if v else 1
            sy = v[1] if len(v) > 1 else sx
            t = (sx, 0, 0, sy, 0, 0)
        elif name == "rotate":
            a = math.radians(v[0] if v else 0)
            ca, sa = math.cos(a), math.sin(a)
            t = (ca, sa, -sa, ca, 0, 0)
            if len(v) >= 3:  # 绕 (cx, cy) 旋转
                cx, cy = v[1], v[2]
                t = mat_mul(mat_mul((1, 0, 0, 1, cx, cy), t), (1, 0, 0, 1, -cx, -cy))
        elif name == "matrix" and len(v) >= 6:
            t = (v[0], v[1], v[2], v[3], v[4], v[5])
        else:
            continue
        m = mat_mul(m, t)
    return m


# ── 路径命令 → 点序列 ────────────────────────────────────


def _arc_to_points(
    x0: float, y0: float, rx: float, ry: float, phi: float,
    large: int, sweep: int, x1: float, y1: float, tol: float = 0.05,
) -> list[tuple[float, float]]:
    """端点式椭圆弧 → 折线（SVG 规范 F.6.5）。"""
    if rx == 0 or ry == 0:
        return [(x1, y1)]
    rx, ry = abs(rx), abs(ry)
    p = math.radians(phi)
    cp, sp = math.cos(p), math.sin(p)
    dx2, dy2 = (x0 - x1) / 2, (y0 - y1) / 2
    x1p = cp * dx2 + sp * dy2
    y1p = -sp * dx2 + cp * dy2
    lam = (x1p * x1p) / (rx * rx) + (y1p * y1p) / (ry * ry)
    if lam > 1:
        s = math.sqrt(lam)
        rx, ry = rx * s, ry * s
    num = rx * rx * ry * ry - rx * rx * y1p * y1p - ry * ry * x1p * x1p
    den = rx * rx * y1p * y1p + ry * ry * x1p * x1p
    co = math.sqrt(max(0.0, num / den)) if den else 0.0
    if large == sweep:
        co = -co
    cxp = co * rx * y1p / ry
    cyp = -co * ry * x1p / rx
    cx = cp * cxp - sp * cyp + (x0 + x1) / 2
    cy = sp * cxp + cp * cyp + (y0 + y1) / 2

    def ang(ux, uy, vx, vy):
        dot = ux * vx + uy * vy
        n = math.hypot(ux, uy) * math.hypot(vx, vy)
        if n == 0:
            return 0.0
        a = math.acos(max(-1.0, min(1.0, dot / n)))
        return -a if (ux * vy - uy * vx) < 0 else a

    th1 = ang(1, 0, (x1p - cxp) / rx, (y1p - cyp) / ry)
    dth = ang((x1p - cxp) / rx, (y1p - cyp) / ry, (-x1p - cxp) / rx, (-y1p - cyp) / ry)
    if sweep == 0 and dth > 0:
        dth -= 2 * math.pi
    elif sweep == 1 and dth < 0:
        dth += 2 * math.pi

    r = max(rx, ry)
    n = max(4, min(720, int(abs(dth) * math.sqrt(r / max(tol, 1e-4)))))
    out = []
    for i in range(1, n + 1):
        t = th1 + dth * i / n
        ex = cx + rx * math.cos(t) * cp - ry * math.sin(t) * sp
        ey = cy + rx * math.cos(t) * sp + ry * math.sin(t) * cp
        out.append((ex, ey))
    return out


def path_to_subpaths(d: str, tol: float = 0.05) -> list[list[tuple[float, float]]]:
    """解析 path d，返回若干子路径（每段折线）。"""
    toks = _tokenize_path(d)
    subpaths: list[list[tuple[float, float]]] = []
    cur: list[tuple[float, float]] = []
    x = y = 0.0
    sx = sy = 0.0  # 当前子路径起点
    last_ctrl = None  # 上一段的三次控制点（用于 S/T）
    prev_cmd = ""

    def flush():
        nonlocal cur
        if len(cur) >= 2:
            subpaths.append(cur)
        cur = []

    i = 0
    while i < len(toks):
        cmd, args = toks[i]
        i += 1
        rel = cmd.islower()
        c = cmd.upper()

        if c == "Z":
            if cur:
                cur.append((sx, sy))
                flush()
            x, y = sx, sy
            last_ctrl = None
            prev_cmd = c
            continue

        # 每个命令的参数个数
        nargs = {"M": 2, "L": 2, "H": 1, "V": 1, "C": 6, "S": 4, "Q": 4, "T": 2, "A": 7}[c]
        k = 0
        first = True
        while k + nargs <= len(args):
            a = args[k:k + nargs]
            k += nargs

            if c == "M":
                nx, ny = (x + a[0], y + a[1]) if rel else (a[0], a[1])
                flush()
                x, y = nx, ny
                sx, sy = nx, ny
                cur = [(x, y)]
                last_ctrl = None
            elif c == "L":
                nx, ny = (x + a[0], y + a[1]) if rel else (a[0], a[1])
                if not cur:
                    cur = [(x, y)]
                cur.append((nx, ny))
                x, y = nx, ny
                last_ctrl = None
            elif c == "H":
                nx = x + a[0] if rel else a[0]
                if not cur:
                    cur = [(x, y)]
                cur.append((nx, y))
                x = nx
                last_ctrl = None
            elif c == "V":
                ny = y + a[0] if rel else a[0]
                if not cur:
                    cur = [(x, y)]
                cur.append((x, ny))
                y = ny
                last_ctrl = None
            elif c in ("C", "S"):
                if c == "C":
                    c1 = (x + a[0], y + a[1]) if rel else (a[0], a[1])
                    c2 = (x + a[2], y + a[3]) if rel else (a[2], a[3])
                    p1 = (x + a[4], y + a[5]) if rel else (a[4], a[5])
                else:
                    if prev_cmd in ("C", "S") and last_ctrl:
                        c1 = (2 * x - last_ctrl[0], 2 * y - last_ctrl[1])
                    else:
                        c1 = (x, y)
                    c2 = (x + a[0], y + a[1]) if rel else (a[0], a[1])
                    p1 = (x + a[2], y + a[3]) if rel else (a[2], a[3])
                if not cur:
                    cur = [(x, y)]
                cur.extend(_bezier((x, y), c1, c2, p1, tol)[1:])
                last_ctrl = c2
                x, y = p1
            elif c in ("Q", "T"):
                if c == "Q":
                    q = (x + a[0], y + a[1]) if rel else (a[0], a[1])
                    p1 = (x + a[2], y + a[3]) if rel else (a[2], a[3])
                else:
                    if prev_cmd in ("Q", "T") and last_ctrl:
                        q = (2 * x - last_ctrl[0], 2 * y - last_ctrl[1])
                    else:
                        q = (x, y)
                    p1 = (x + a[0], y + a[1]) if rel else (a[0], a[1])
                # 二次转三次
                c1 = (x + 2 / 3 * (q[0] - x), y + 2 / 3 * (q[1] - y))
                c2 = (p1[0] + 2 / 3 * (q[0] - p1[0]), p1[1] + 2 / 3 * (q[1] - p1[1]))
                if not cur:
                    cur = [(x, y)]
                cur.extend(_bezier((x, y), c1, c2, p1, tol)[1:])
                last_ctrl = q
                x, y = p1
            elif c == "A":
                rx, ry, phi, large, sweep = a[0], a[1], a[2], int(a[3]), int(a[4])
                nx, ny = (x + a[5], y + a[6]) if rel else (a[5], a[6])
                if not cur:
                    cur = [(x, y)]
                cur.extend(_arc_to_points(x, y, rx, ry, phi, large, sweep, nx, ny, tol))
                x, y = nx, ny
                last_ctrl = None
            prev_cmd = c
            first = False

        # 处理重复参数（如 M 后面跟多组坐标 = 隐式 L）
        if c == "M" and k == 0:
            pass

    flush()
    return subpaths


def _bezier(p0, c1, c2, p1, tol=0.05) -> list[tuple[float, float]]:
    """自适应三次贝塞尔离散（p0 起点，c1/c2 控制点，p1 终点）。"""
    def flat(a, b, cc, d) -> bool:
        dx, dy = d[0] - a[0], d[1] - a[1]
        n = math.hypot(dx, dy)
        if n < 1e-9:
            return True
        d1 = abs((b[0] - a[0]) * dy - (b[1] - a[1]) * dx) / n
        d2 = abs((cc[0] - a[0]) * dy - (cc[1] - a[1]) * dx) / n
        return max(d1, d2) <= tol

    out = [p0]

    def rec(a, b, cc, d, depth=0):
        if depth > 16 or flat(a, b, cc, d):
            out.append(d)
            return
        ab = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
        bc = ((b[0] + cc[0]) / 2, (b[1] + cc[1]) / 2)
        cd = ((cc[0] + d[0]) / 2, (cc[1] + d[1]) / 2)
        abc = ((ab[0] + bc[0]) / 2, (ab[1] + bc[1]) / 2)
        bcd = ((bc[0] + cd[0]) / 2, (bc[1] + cd[1]) / 2)
        mid = ((abc[0] + bcd[0]) / 2, (abc[1] + bcd[1]) / 2)
        rec(a, ab, abc, mid, depth + 1)
        rec(mid, bcd, cd, d, depth + 1)

    rec(p0, c1, c2, p1)
    return out


# ── 元素 → 子路径 ───────────────────────────────────────


def _num_attr(el, name, default=0.0) -> float:
    v = el.get(name)
    if not v:
        return default
    try:
        return float(NUM_RE.search(v).group())
    except Exception:
        return default


def _rect_subpath(el) -> list[list[tuple[float, float]]]:
    x, y = _num_attr(el, "x"), _num_attr(el, "y")
    w, h = _num_attr(el, "width"), _num_attr(el, "height")
    if w <= 0 or h <= 0:
        return []
    return [[(x, y), (x + w, y), (x + w, y + h), (x, y + h), (x, y)]]


def _ellipse_subpath(el, is_circle: bool) -> list[list[tuple[float, float]]]:
    cx, cy = _num_attr(el, "cx"), _num_attr(el, "cy")
    if is_circle:
        rx = ry = _num_attr(el, "r")
    else:
        rx, ry = _num_attr(el, "rx"), _num_attr(el, "ry")
    if rx <= 0 or ry <= 0:
        return []
    n = max(16, min(512, int(2 * math.pi * max(rx, ry) / 0.2)))
    pts = [
        (cx + rx * math.cos(2 * math.pi * i / n), cy + ry * math.sin(2 * math.pi * i / n))
        for i in range(n)
    ]
    pts.append(pts[0])
    return [pts]


def _points_subpath(el, close: bool) -> list[list[tuple[float, float]]]:
    v = _nums(el.get("points", ""))
    pts = [(v[i], v[i + 1]) for i in range(0, len(v) - 1, 2)]
    if len(pts) < 2:
        return []
    if close and pts[0] != pts[-1]:
        pts.append(pts[0])
    return [pts]


def _line_subpath(el) -> list[list[tuple[float, float]]]:
    x1, y1 = _num_attr(el, "x1"), _num_attr(el, "y1")
    x2, y2 = _num_attr(el, "x2"), _num_attr(el, "y2")
    if x1 == x2 and y1 == y2:
        return []
    return [[(x1, y1), (x2, y2)]]


# ── 主入口 ──────────────────────────────────────────────


@dataclass
class SvgImport:
    subpaths: list[list[tuple[float, float]]] = field(default_factory=list)
    width: float = 0.0
    height: float = 0.0
    warnings: list[str] = field(default_factory=list)
    unsupported: list[str] = field(default_factory=list)


def parse_svg(text: str, tol: float = 0.05, flip_y: bool = True) -> SvgImport:
    """把 SVG 文本解析成 mm 坐标下的子路径列表。

    ⚠️ flip_y：SVG 的 Y 轴向下，本工程的 Y 轴向上。默认翻转，
    否则导入的图会上下颠倒。翻转基准用 viewBox 高度。
    """
    res = SvgImport()
    try:
        root = ET.fromstring(text)
    except Exception as e:
        res.warnings.append(f"XML 解析失败: {e}")
        return res

    def tag(el) -> str:
        return el.tag.split("}")[-1] if isinstance(el.tag, str) else ""

    # viewBox / width / height → 缩放
    vb = root.get("viewBox") or root.get("viewbox")
    sx = sy = 1.0
    if vb:
        v = _nums(vb)
        if len(v) == 4 and v[2] > 0 and v[3] > 0:
            res.width, res.height = v[2], v[3]
    if res.width <= 0:
        res.width = _num_attr(root, "width", 100.0) or 100.0
    if res.height <= 0:
        res.height = _num_attr(root, "height", 100.0) or 100.0
    sx, sy = 1.0, 1.0  # 1 user unit = 1 mm

    def walk(el, mat: Mat):
        t = tag(el)
        m = mat_mul(mat, parse_transform(el.get("transform", "")))

        subs: list[list[tuple[float, float]]] = []
        if t == "path":
            d = el.get("d", "")
            if d:
                subs = path_to_subpaths(d, tol)
        elif t == "rect":
            subs = _rect_subpath(el)
        elif t == "circle":
            subs = _ellipse_subpath(el, True)
        elif t == "ellipse":
            subs = _ellipse_subpath(el, False)
        elif t == "polyline":
            subs = _points_subpath(el, False)
        elif t == "polygon":
            subs = _points_subpath(el, True)
        elif t == "line":
            subs = _line_subpath(el)
        elif t in ("g", "svg", "a", "symbol", "defs"):
            pass
        elif t in ("text", "image", "use", "tspan", "filter", "clipPath", "mask", "pattern"):
            if t not in res.unsupported:
                res.unsupported.append(t)
        elif t:
            pass

        for sp in subs:
            mapped = [mat_apply(m, px * sx, py * sy) for px, py in sp]
            if len(mapped) >= 2:
                res.subpaths.append(mapped)

        for ch in list(el):
            walk(ch, m)

    walk(root, IDENTITY)

    # SVG 的 Y 轴向下 → 本工程 Y 轴向上，必须翻转
    if flip_y and res.subpaths:
        h = res.height
        res.subpaths = [[(px, h - py) for px, py in sp] for sp in res.subpaths]

    if res.unsupported:
        res.warnings.append(
            "以下元素不支持，已忽略：" + "、".join(f"<{u}>" for u in res.unsupported)
        )
    if not res.subpaths:
        res.warnings.append("没有解析到任何路径（可能是纯位图或空 SVG）")
    return res


def parse_svg_file(path: str, tol: float = 0.05) -> SvgImport:
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return parse_svg(f.read(), tol)
