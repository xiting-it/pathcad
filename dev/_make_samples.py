"""生成一组示例 STL 模型，供 pathCAM 导入试用。

不用下载别人的文件：程序化生成，几何可控、可复现、无版权问题。
都是 DIW（气动挤出）比较有代表性的形状：旋转体、齿轮、扭曲塔、环面、格栅立方。
"""

from __future__ import annotations

import math
import struct
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "samples"
TAU = math.pi * 2


# ── STL 写出 ────────────────────────────────────────────


def write_stl(path: Path, tris: list[tuple]) -> None:
    """二进制 STL。tris 每项是 ((x,y,z),(x,y,z),(x,y,z))。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as f:
        f.write(b"\0" * 80)
        f.write(struct.pack("<I", len(tris)))
        for a, b, c in tris:
            # 法向量用叉积算，切片器不一定用，但写对了更规范
            ux, uy, uz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
            vx, vy, vz = c[0] - a[0], c[1] - a[1], c[2] - a[2]
            nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
            ln = math.sqrt(nx * nx + ny * ny + nz * nz) or 1.0
            f.write(struct.pack("<3f", nx / ln, ny / ln, nz / ln))
            for p in (a, b, c):
                f.write(struct.pack("<3f", *p))
            f.write(struct.pack("<H", 0))


# ── 几何工具 ────────────────────────────────────────────


def quad(a, b, c, d) -> list:
    """四边形拆两个三角。"""
    return [(a, b, c), (a, c, d)]


def revolve(profile: list[tuple[float, float]], n_theta: int,
            rib: int = 0, rib_amp: float = 0.0) -> list:
    """把 (r, z) 剖面绕 Z 轴旋转成实体。

    rib/rib_amp 给半径加正弦起伏，做出竖棱或花瓣截面。
    profile 首尾若 r=0 会自动收成尖点。
    """
    tris = []
    rings = []
    for i in range(n_theta):
        th = TAU * i / n_theta
        # 花瓣：rib 条棱
        k = 1.0 + rib_amp * math.cos(rib * th) if rib else 1.0
        ring = []
        for r, z in profile:
            rr = r * k
            ring.append((rr * math.cos(th), rr * math.sin(th), z))
        rings.append(ring)

    for i in range(n_theta):
        a_ring = rings[i]
        b_ring = rings[(i + 1) % n_theta]
        for j in range(len(profile) - 1):
            a, b = a_ring[j], a_ring[j + 1]
            c, d = b_ring[j + 1], b_ring[j]
            # 退化（两端都是轴心）就跳过
            if (a[0] == 0 and a[1] == 0 and b[0] == 0 and b[1] == 0):
                continue
            if (c[0] == 0 and c[1] == 0 and d[0] == 0 and d[1] == 0):
                continue
            tris.extend(quad(a, b, c, d))
    return tris


def extrude(poly: list[tuple[float, float]], z0: float, z1: float) -> list:
    """把闭合二维多边形拉成柱体（含上下盖）。poly 不用重复首点。"""
    tris = []
    n = len(poly)
    # 侧面
    for i in range(n):
        x0, y0 = poly[i]
        x1, y1 = poly[(i + 1) % n]
        tris.extend(quad((x0, y0, z0), (x1, y1, z0), (x1, y1, z1), (x0, y0, z1)))
    # 盖：扇形三角化（多边形是凸的才正确，齿轮用凸多边形近似即可）
    for i in range(1, n - 1):
        x0, y0 = poly[0]
        x1, y1 = poly[i]
        x2, y2 = poly[i + 1]
        tris.append(((x0, y0, z1), (x1, y1, z1), (x2, y2, z1)))
        tris.append(((x0, y0, z0), (x2, y2, z0), (x1, y1, z0)))
    return tris


# ── 各个模型 ────────────────────────────────────────────


def make_vase() -> list:
    """花瓶：直壁 + 外扩口 + 竖棱。经典打印测试件。"""
    prof = []
    n = 40
    for i in range(n + 1):
        t = i / n
        z = t * 40.0
        # 底部收一点、中段鼓、口部略缩
        r = 8.0 + 4.0 * math.sin(t * math.pi) - 2.0 * t
        prof.append((max(r, 0.5), z))
    prof.insert(0, (0.0, 0.0))  # 底封
    return revolve(prof, 72, rib=10, rib_amp=0.06)


def make_gear() -> list:
    """直齿圆柱齿轮：16 齿，外径 30mm。"""
    teeth, r_out, r_in = 16, 15.0, 11.5
    poly = []
    for i in range(teeth):
        th = TAU * i / teeth
        step = TAU / teeth
        # 每个齿：齿根 - 齿顶 - 齿顶 - 齿根
        for frac, rad in ((0.00, r_in), (0.18, r_out), (0.42, r_out), (0.60, r_in)):
            a = th + step * frac
            poly.append((rad * math.cos(a), rad * math.sin(a)))
    # 中心也挖空的话要做内环，这里保持实心（切片更稳）
    return extrude(poly, 0.0, 8.0)


def make_twisted_tower() -> list:
    """扭曲方塔：每层转一点，考验切片对非凸轮廓的处理。"""
    sides, layers = 4, 60
    h, r0 = 45.0, 12.0
    rings = []
    for k in range(layers + 1):
        t = k / layers
        z = t * h
        twist = t * math.pi * 0.75          # 总扭转 135°
        r = r0 * (1.0 - 0.35 * t)           # 往上收
        ring = []
        for i in range(sides):
            a = TAU * i / sides + twist
            ring.append((r * math.cos(a), r * math.sin(a), z))
        rings.append(ring)

    tris = []
    for k in range(layers):
        a_ring, b_ring = rings[k], rings[k + 1]
        for i in range(sides):
            j = (i + 1) % sides
            tris.extend(quad(a_ring[i], a_ring[j], b_ring[j], b_ring[i]))
    # 底盖
    tris.append((rings[0][0], rings[0][2], rings[0][1]))
    tris.append((rings[0][0], rings[0][3], rings[0][2]))
    # 顶盖
    tris.append((rings[-1][0], rings[-1][1], rings[-1][2]))
    tris.append((rings[-1][0], rings[-1][2], rings[-1][3]))
    return tris


def make_torus() -> list:
    """环面：外径 36，管径 7。闭合曲面，考验轮廓闭合性。"""
    R, r = 18.0, 7.0
    nu, nv = 64, 28
    grid = []
    for i in range(nu):
        th = TAU * i / nu
        row = []
        for j in range(nv):
            ph = TAU * j / nv
            x = (R + r * math.cos(ph)) * math.cos(th)
            y = (R + r * math.cos(ph)) * math.sin(th)
            z = r * math.sin(ph) + r   # 抬到 z>=0
            row.append((x, y, z))
        grid.append(row)

    tris = []
    for i in range(nu):
        i2 = (i + 1) % nu
        for j in range(nv):
            j2 = (j + 1) % nv
            tris.extend(quad(grid[i][j], grid[i2][j], grid[i2][j2], grid[i][j2]))
    return tris


def make_lattice_cube() -> list:
    """格栅立方：和视频里那个格栅块同构，但做成 3D 立方。

    由 3×3×3 的细梁拼成，中间是通的孔。
    """
    size, beam = 24.0, 3.0
    tris = []
    half = size / 2

    # 三个方向各若干根梁，用方柱近似
    def box(x0, y0, z0, x1, y1, z1):
        p = [
            (x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0),
            (x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1),
        ]
        f = [(0, 1, 2, 3), (4, 7, 6, 5), (0, 4, 5, 1),
             (1, 5, 6, 2), (2, 6, 7, 3), (3, 7, 4, 0)]
        for a, b, c, d in f:
            tris.extend(quad(p[a], p[b], p[c], p[d]))

    offs = (-half + beam / 2, 0.0, half - beam / 2)
    for o1 in offs:
        for o2 in offs:
            box(-half, o1 - beam / 2, o2 - beam / 2, half, o1 + beam / 2, o2 + beam / 2)
            box(o1 - beam / 2, -half, o2 - beam / 2, o1 + beam / 2, half, o2 + beam / 2)
            box(o1 - beam / 2, o2 - beam / 2, -half, o1 + beam / 2, o2 + beam / 2, half)
    return tris


def make_star_vase() -> list:
    """星形花器：5 角星截面往上收，带轻微扭转。"""
    prof = []
    n = 36
    for i in range(n + 1):
        t = i / n
        z = t * 35.0
        r = 14.0 * (1.0 - 0.45 * t)
        prof.append((r, z))
    prof.insert(0, (0.0, 0.0))
    tris = revolve(prof, 60, rib=5, rib_amp=0.28)
    return tris


MODELS = [
    ("01-花瓶.stl", make_vase, "花瓶 · 直径约 24mm 高 40mm · 10 条竖棱"),
    ("02-齿轮.stl", make_gear, "直齿齿轮 · 外径 30mm · 16 齿 · 高 8mm"),
    ("03-扭曲塔.stl", make_twisted_tower, "扭曲方塔 · 底 24mm 高 45mm · 总扭转 135°"),
    ("04-环面.stl", make_torus, "环面 · 外径 36mm · 管径 7mm"),
    ("05-格栅立方.stl", make_lattice_cube, "格栅立方 · 24mm · 3×3×3 细梁 · 中间通透"),
    ("06-星形花器.stl", make_star_vase, "星形花器 · 5 角 · 底径 28mm 高 35mm"),
]


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    print(f"输出目录：{OUT}\n")
    for name, fn, desc in MODELS:
        tris = fn()
        path = OUT / name
        write_stl(path, tris)
        kb = path.stat().st_size / 1024
        print(f"  {name:18s} {len(tris):6d} 三角面  {kb:7.1f} KB   {desc}")
    print(f"\n共 {len(MODELS)} 个模型")


if __name__ == "__main__":
    main()
