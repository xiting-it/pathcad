"""网格导入与切片：STL / OBJ → 逐层轮廓。

Blender 导出的 STL / OBJ 是三角网格。这里把它沿 Z 切成一层层的轮廓线，
每层轮廓再交给现有的填充/编译管线。

只依赖标准库（STL 解析手写），切片用纯 Python 三角-平面求交。
"""

from __future__ import annotations

import math
import struct
from dataclasses import dataclass, field

Vec3 = tuple[float, float, float]
Tri = tuple[Vec3, Vec3, Vec3]


# ══════════════════════════════════════════════════════════
# STL 解析
# ══════════════════════════════════════════════════════════


def parse_stl(data: bytes) -> tuple[list[Tri], list[str]]:
    """解析 STL，自动识别二进制/ASCII。"""
    warns: list[str] = []
    if not data:
        return [], ["文件为空"]

    # 二进制判定：文件头 80 字节 + 4 字节数量，且总长度吻合
    if len(data) >= 84:
        n = struct.unpack_from("<I", data, 80)[0]
        expected = 84 + n * 50
        if n > 0 and abs(expected - len(data)) <= 4:
            return _parse_stl_binary(data, n, warns)

    # ASCII
    head = data[:512].lstrip().lower()
    if head.startswith(b"solid"):
        tris = _parse_stl_ascii(data, warns)
        if tris:
            return tris, warns
        # ASCII 解析失败再退回二进制
        if len(data) >= 84:
            n = struct.unpack_from("<I", data, 80)[0]
            if 0 < n < 10_000_000:
                return _parse_stl_binary(data, n, warns)
    warns.append("无法识别的 STL 格式（既不是标准二进制也不是标准 ASCII）")
    return [], warns


def _parse_stl_binary(data: bytes, n: int, warns: list[str]) -> tuple[list[Tri], list[str]]:
    """二进制 STL：每个三角面 50 字节 = 法向量(12) + 3 顶点(36) + 属性(2)。"""
    tris: list[Tri] = []
    off = 84
    for _ in range(n):
        if off + 50 > len(data):
            warns.append("STL 数据被截断，只解析了部分三角形")
            break
        # 跳过 12 字节法向量，只读 9 个 float（3 个顶点 × 3 轴）
        vals = struct.unpack_from("<9f", data, off + 12)
        tris.append(
            (
                (vals[0], vals[1], vals[2]),
                (vals[3], vals[4], vals[5]),
                (vals[6], vals[7], vals[8]),
            )
        )
        off += 50
    if not tris:
        warns.append("STL 里没有三角形")
    return tris, warns


def _parse_stl_ascii(data: bytes, warns: list[str]) -> list[Tri]:
    tris: list[Tri] = []
    cur: list[Vec3] = []
    try:
        text = data.decode("utf-8", errors="replace")
    except Exception:
        return []
    for line in text.splitlines():
        s = line.strip()
        if not s.lower().startswith("vertex"):
            continue
        parts = s.split()
        if len(parts) < 4:
            continue
        try:
            v = (float(parts[1]), float(parts[2]), float(parts[3]))
        except ValueError:
            continue
        cur.append(v)
        if len(cur) == 3:
            tris.append((cur[0], cur[1], cur[2]))
            cur = []
    return tris


# ══════════════════════════════════════════════════════════
# OBJ 解析
# ══════════════════════════════════════════════════════════


def parse_obj(text: str) -> tuple[list[Tri], list[str]]:
    warns: list[str] = []
    verts: list[Vec3] = []
    tris: list[Tri] = []
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if s.startswith("v "):
            p = s.split()
            if len(p) >= 4:
                try:
                    verts.append((float(p[1]), float(p[2]), float(p[3])))
                except ValueError:
                    pass
        elif s.startswith("f "):
            p = s.split()[1:]
            idx: list[int] = []
            for tok in p:
                # 支持 v / v/vt / v/vt/vn / v//vn
                head = tok.split("/")[0]
                if not head:
                    continue
                try:
                    i = int(head)
                except ValueError:
                    continue
                idx.append(i - 1 if i > 0 else len(verts) + i)
            if len(idx) < 3:
                continue
            # 扇形三角化（支持 n 边形）
            for k in range(1, len(idx) - 1):
                a, b, c = idx[0], idx[k], idx[k + 1]
                if 0 <= a < len(verts) and 0 <= b < len(verts) and 0 <= c < len(verts):
                    tris.append((verts[a], verts[b], verts[c]))

    if not verts:
        warns.append("OBJ 里没有顶点数据")
    if not tris:
        warns.append("OBJ 里没有面数据")
    if "vt " not in text and "vn " not in text:
        pass  # 正常，纯几何
    return tris, warns


# ══════════════════════════════════════════════════════════
# 变换与包围盒
# ══════════════════════════════════════════════════════════


def mesh_bounds(tris: list[Tri]) -> tuple[float, float, float, float, float, float]:
    xs = [v[0] for t in tris for v in t]
    ys = [v[1] for t in tris for v in t]
    zs = [v[2] for t in tris for v in t]
    if not xs:
        return (0, 0, 0, 0, 0, 0)
    return (min(xs), min(ys), min(zs), max(xs), max(ys), max(zs))


def transform_mesh(
    tris: list[Tri],
    scale: float = 1.0,
    offset: Vec3 = (0.0, 0.0, 0.0),
) -> list[Tri]:
    ox, oy, oz = offset
    return [
        tuple((v[0] * scale + ox, v[1] * scale + oy, v[2] * scale + oz) for v in t)
        for t in tris
    ]


# ══════════════════════════════════════════════════════════
# 切片
# ══════════════════════════════════════════════════════════


@dataclass
class SliceResult:
    layers: list[tuple[float, list[list[tuple[float, float]]]]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    z_min: float = 0.0
    z_max: float = 0.0


def _tri_plane_segment(t: Tri, z: float, eps: float = 1e-9):
    """三角形与 z=const 平面的交线段。返回 ((x0,y0),(x1,y1)) 或 None。"""
    pts: list[tuple[float, float]] = []
    for i in range(3):
        a = t[i]
        b = t[(i + 1) % 3]
        da = a[2] - z
        db = b[2] - z
        if abs(da) < eps and abs(db) < eps:
            continue  # 整条边在平面上，忽略（避免重复）
        if (da > 0 and db > 0) or (da < 0 and db < 0):
            continue
        if abs(da) < eps:
            pts.append((a[0], a[1]))
        elif abs(db) < eps:
            pts.append((b[0], b[1]))
        elif (da > 0) != (db > 0):
            tt = da / (da - db)
            pts.append((a[0] + (b[0] - a[0]) * tt, a[1] + (b[1] - a[1]) * tt))
    # 去重
    uniq: list[tuple[float, float]] = []
    for p in pts:
        if not any(abs(p[0] - q[0]) < 1e-9 and abs(p[1] - q[1]) < 1e-9 for q in uniq):
            uniq.append(p)
    if len(uniq) < 2:
        return None
    return (uniq[0], uniq[1])


def slice_at_z(tris: list[Tri], z: float, tol: float = 1e-4) -> list[list[tuple[float, float]]]:
    """在高度 z 切一刀，返回若干闭合/开放轮廓。"""
    segs: list[tuple[tuple[float, float], tuple[float, float]]] = []
    for t in tris:
        s = _tri_plane_segment(t, z)
        if s:
            segs.append(s)
    if not segs:
        return []
    return _chain_segments(segs, tol)


def _key(p: tuple[float, float], tol: float) -> tuple[int, int]:
    return (round(p[0] / tol), round(p[1] / tol))


def _chain_segments(
    segs: list[tuple[tuple[float, float], tuple[float, float]]],
    tol: float = 1e-4,
) -> list[list[tuple[float, float]]]:
    """把无序线段首尾相接成轮廓。"""
    # 端点桶
    buckets: dict[tuple[int, int], list[int]] = {}
    for i, (a, b) in enumerate(segs):
        buckets.setdefault(_key(a, tol), []).append(i)
        buckets.setdefault(_key(b, tol), []).append(i)

    used = [False] * len(segs)
    loops: list[list[tuple[float, float]]] = []

    def neighbors(p):
        return [i for i in buckets.get(_key(p, tol), []) if not used[i]]

    for start_i in range(len(segs)):
        if used[start_i]:
            continue
        used[start_i] = True
        a, b = segs[start_i]
        path = [a, b]
        cur = b

        # 向前延伸
        guard = 0
        while guard < len(segs) + 2:
            guard += 1
            nxt = None
            for i in neighbors(cur):
                p0, p1 = segs[i]
                if _key(p0, tol) == _key(cur, tol):
                    nxt = (i, p1)
                elif _key(p1, tol) == _key(cur, tol):
                    nxt = (i, p0)
                if nxt:
                    break
            if not nxt:
                break
            i, p = nxt
            used[i] = True
            path.append(p)
            cur = p
            if _key(cur, tol) == _key(path[0], tol):
                break  # 闭合

        # 向后延伸（起点方向）
        cur = path[0]
        guard = 0
        while guard < len(segs) + 2:
            guard += 1
            nxt = None
            for i in neighbors(cur):
                p0, p1 = segs[i]
                if _key(p0, tol) == _key(cur, tol):
                    nxt = (i, p1)
                elif _key(p1, tol) == _key(cur, tol):
                    nxt = (i, p0)
                if nxt:
                    break
            if not nxt:
                break
            i, p = nxt
            used[i] = True
            path.insert(0, p)
            cur = p
            if _key(cur, tol) == _key(path[-1], tol):
                break

        # 去重相邻重复点
        clean: list[tuple[float, float]] = []
        for p in path:
            if not clean or abs(p[0] - clean[-1][0]) > 1e-9 or abs(p[1] - clean[-1][1]) > 1e-9:
                clean.append(p)
        if len(clean) >= 3:
            # 闭合的话补上首点
            if _key(clean[0], tol) != _key(clean[-1], tol):
                pass  # 开放轮廓，保留
            loops.append(clean)

    return loops


def slice_layers(
    tris: list[Tri],
    layer_height: float = 0.4,
    z_start: float | None = None,
    max_layers: int = 400,
    tol: float = 1e-4,
) -> SliceResult:
    """把网格按层高切片，返回每层的轮廓。"""
    res = SliceResult()
    if not tris:
        res.warnings.append("网格为空")
        return res
    if layer_height <= 0:
        res.warnings.append("层高必须大于 0")
        return res

    _, _, zmin, _, _, zmax = mesh_bounds(tris)
    res.z_min, res.z_max = zmin, zmax
    height = zmax - zmin
    if height <= 1e-9:
        res.warnings.append("网格高度为 0（可能是个平面）")
        return res

    n = int(math.ceil(height / layer_height))
    if n > max_layers:
        res.warnings.append(f"层数 {n} 超过上限 {max_layers}，已截断")
        n = max_layers

    z0 = z_start if z_start is not None else zmin + layer_height / 2
    empty_streak = 0
    for i in range(n):
        z = z0 + i * layer_height
        if z > zmax:
            break
        loops = slice_at_z(tris, z, tol)
        if loops:
            empty_streak = 0
            res.layers.append((round(z - zmin, 4), loops))
        else:
            empty_streak += 1
            if empty_streak > 8:
                break
    if not res.layers:
        res.warnings.append("没有切出任何层（模型可能没有体积）")
    return res


def load_mesh(name: str, data: bytes) -> tuple[list[Tri], list[str]]:
    """按扩展名/内容自动选择解析器。"""
    low = name.lower()
    if low.endswith(".obj"):
        try:
            text = data.decode("utf-8", errors="replace")
        except Exception:
            return [], ["OBJ 解码失败"]
        return parse_obj(text)
    if low.endswith(".stl"):
        return parse_stl(data)
    # 未知扩展名：先试 OBJ 文本，再试 STL
    head = data[:200].lstrip()
    if head.startswith(b"v ") or b"\nv " in data[:2000]:
        try:
            return parse_obj(data.decode("utf-8", errors="replace"))
        except Exception:
            pass
    return parse_stl(data)
