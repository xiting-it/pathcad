"""分析 05-格栅立方.stl 为什么编译出 9379 行。

逐层看：轮廓点数、填充笔画数、生成的指令数。
"""

from __future__ import annotations

import sys

sys.path.insert(0, ".")

from app.mesh import load_mesh, slice_layers
from app.model import (
    Layer,
    Machine,
    Point,
    Process,
    Project,
    Source,
    Stroke,
    migrate_project,
)
from app.compiler import compile_project
from app.dialects import make_dialect
from app.infill import fill_grid

STL = r"B:\Users\Echo\.dsh\attachments\v1\files\c0\c0a2a50a67ea18fd315f5dfeeab7d67ed9ab3fb017702a9f83cef51bd1d0905d\05-格栅立方.stl"


def main() -> None:
    with open(STL, "rb") as f:
        data = f.read()

    tris, warns = load_mesh("05-格栅立方.stl", data)
    print(f"三角面数: {len(tris)}   解析警告: {warns[:2] if warns else chr(26080)}")

    res = slice_layers(tris, layer_height=0.4, max_layers=400)
    print(f"层数: {len(res.layers)}    z: {res.z_min:.2f} ~ {res.z_max:.2f}")
    print(f"警告: {res.warnings[:3] if res.warnings else '无'}")
    print()

    # 逐层统计
    print(f"{'层':>3} {'z':>6} {'轮廓条':>7} {'轮廓点数':>9} {'包围盒':>24}")
    print("-" * 60)
    total_pts = 0
    for i, (z, paths) in enumerate(res.layers[:8]):
        npts = sum(len(p) for p in paths)
        total_pts += npts
        if paths:
            xs = [p[0] for path in paths for p in path]
            ys = [p[1] for path in paths for p in path]
            bb = f"[{min(xs):.1f},{min(ys):.1f}] - [{max(xs):.1f},{max(ys):.1f}]"
        else:
            bb = "-"
        print(f"{i+1:3d} {z:6.2f} {len(paths):7d} {npts:9d} {bb:>24}")
    print("...")

    # 全层点数
    all_pts = sum(len(p) for _, paths in res.layers for p in paths)
    print(f"\n全部轮廓总点数: {all_pts}")

    # 构造工程：轮廓 + 不填充
    layers_no_fill = []
    for i, (z, paths) in enumerate(res.layers):
        strokes = [
            Stroke(
                id=f"L{i}s{j}",
                points=[Point(x=x, y=y) for x, y in path],
                closed=True,
                kind="contour",
            )
            for j, path in enumerate(paths)
        ]
        layers_no_fill.append(Layer(id=f"L{i}", z=z, repeat=1, z_step=0.4, strokes=strokes))

    proj = Project(
        machine=Machine(name="h5", travel_x=200, travel_y=200, travel_z=200),
        process=Process(),
        layers=layers_no_fill,
    )
    c = compile_project(proj, make_dialect("h5"))
    print("\n【只轮廓，不填充】")
    print(f"  笔画 {c.stroke_count}   指令 {len(c.lines)} 行")
    print(f"  预计 {len(c.lines) * 0.55 / 60:.1f} 分钟（按 0.55 秒/行）")

    # 加填充
    layers_fill = []
    for i, (z, paths) in enumerate(res.layers):
        strokes = [
            Stroke(id=f"L{i}s{j}", points=[Point(x=x, y=y) for x, y in path],
                   closed=True, kind="contour")
            for j, path in enumerate(paths)
        ]
        # 对每条闭合轮廓做格栅填充
        for j, path in enumerate(paths):
            if len(path) < 3:
                continue
            pts = list(path)
            if pts[0] != pts[-1]:
                pts = pts + [pts[0]]
            try:
                segs = fill_grid(pts, spacing=2.0, inset=0.0, min_len=0.4)
            except Exception:
                segs = []
            for k, seg in enumerate(segs):
                strokes.append(
                    Stroke(id=f"L{i}f{j}_{k}",
                           points=[Point(x=x, y=y) for x, y in seg],
                           kind="fill")
                )
        layers_fill.append(Layer(id=f"L{i}", z=z, repeat=1, z_step=0.4, strokes=strokes))

    proj2 = Project(
        machine=Machine(name="h5", travel_x=200, travel_y=200, travel_z=200),
        process=Process(),
        layers=layers_fill,
    )
    c2 = compile_project(proj2, make_dialect("h5"))
    print("\n【轮廓 + 2mm 格栅填充】")
    print(f"  笔画 {c2.stroke_count}   指令 {len(c2.lines)} 行")
    print(f"  预计 {len(c2.lines) * 0.55 / 60:.1f} 分钟（按 0.55 秒/行）")


if __name__ == "__main__":
    main()
