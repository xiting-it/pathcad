"""FastAPI 后端。

接口：
  POST /api/compile     工程 → G-code + 仿真时间线
  POST /api/fill        图元 → 填充路径
  POST /api/discretize  图元 → 离散点
  GET  /api/health
  GET  /                Web 界面
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .compiler import compile_project, estimate_time
from .dialects import make_dialect
from .geometry import (
    bbox_of,
    discretize_source,
    fill_concentric,
    fill_scanline,
    merge_paths,
    sort_paths,
    to_points,
)
from .model import DIALECTS, IOEvent, Layer, Point, Process, Project, Source, Stroke
from .simulator import find_io_transitions, simulate


def _root() -> Path:
    """资源根目录。

    开发时：app/ 的上一级
    打包后：main.py 会设 PATHCAM_ROOT 指向 sys._MEIPASS
    """
    env = os.environ.get("PATHCAM_ROOT")
    if env:
        return Path(env)
    return Path(__file__).resolve().parent.parent


ROOT = _root()
WEB = ROOT / "web"

from .version import (
    APP_DESC,
    APP_NAME,
    APP_SLUG,
    APP_TITLE,
    LEGACY_NAMES,
    VERSION,
)

app = FastAPI(title=APP_NAME, description=APP_DESC, version=VERSION)


@app.on_event("startup")
def _startup():
    """初始化日志：控制台只打关键事件，文件写到 <ROOT>/data/log/。"""
    from .logger import SRC_SYS, init

    # 打包后 ROOT 是 _MEIPASS（临时解压目录），日志要写到 exe 旁边
    data_root = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else ROOT
    lg = init(data_root, console=True, file=True)
    lg.info(SRC_SYS, f"{APP_NAME} {VERSION} 启动　root={ROOT}")
    lg.info(SRC_SYS, f"日志写入 {data_root / 'data' / 'log'}")


# ── 请求模型 ────────────────────────────────────────────


class CompileReq(BaseModel):
    project: Project
    dialect: str = Field("lc", description="lc | marlin | klipper")
    safe_z: float | None = None
    # 方言参数
    pin_main: int | None = None
    pin_purge: int | None = None


class FillReq(BaseModel):
    source: Source
    pattern: str = Field(
        "grid",
        description="grid=格栅(视频同款) | honeycomb=蜂窝 | scanline=平行扫描 | concentric=同心轮廓",
    )
    spacing: float = 1.0
    angle: float = 0.0
    inset: float = 0.0
    min_len: float = 1.5
    sort: bool = True
    merge: bool = Field(
        False,
        description="合并首尾相接的路径。**DIW 场景默认关闭**："
        "合并后连接两行扫描线的斜段会带着阀门开着走，会真的挤出材料。",
    )
    stagger: bool = Field(False, description="格栅两条方向的筋错开半格")
    outline: bool = Field(True, description="格栅填充是否附带外轮廓")


class FillAllReq(BaseModel):
    """一键全层填充：把所有层的轮廓都填上。"""

    project: Project
    pattern: str = Field(
        "grid",
        description="grid=格栅(视频同款) | honeycomb=蜂窝 | scanline=平行扫描 | concentric=同心轮廓",
    )
    spacing: float = Field(1.5, gt=0)
    angle: float = 0.0
    inset: float = Field(0.4, ge=0)
    min_len: float = Field(1.5, gt=0)
    skip_filled: bool = Field(True, description="跳过已经填充过的层")
    replace_existing: bool = Field(False, description="替换已有的填充线而不是叠加")
    stagger: bool = Field(False, description="格栅两条方向的筋错开半格")
    outline: bool = Field(True, description="格栅填充是否附带外轮廓")


class DiscretizeReq(BaseModel):
    source: Source


class SvgImportReq(BaseModel):
    svg: str = Field(..., description="SVG 文本")
    scale: float = Field(1.0, gt=0, description="额外缩放，1.0 = 1 user unit 当 1mm")
    tol: float = Field(0.05, gt=0, description="曲线离散弦高误差 mm")
    flip_y: bool = Field(True, description="SVG 的 Y 轴向下，本工程向上，默认翻转")
    only_closed: bool = Field(False, description="只要闭合路径（适合填充）")
    max_paths: int = Field(2000, gt=0)


class MeshSliceReq(BaseModel):
    """网格（STL/OBJ）切片请求。前端把文件内容 base64 传过来。"""

    filename: str
    data_b64: str
    scale: float = Field(1.0, gt=0, description="缩放，1.0 = 模型单位当 mm")
    layer_height: float = Field(0.4, gt=0)
    max_layers: int = Field(200, gt=0, le=2000)
    # 摆放：平移到哪个位置
    offset_x: float = 0.0
    offset_y: float = 0.0
    # 是否只保留最大轮廓（避免细碎噪点）
    only_largest: bool = False


# ── 工具 ────────────────────────────────────────────────


def _dialect_kw(req: CompileReq) -> dict:
    kw: dict = {}
    if req.pin_main is not None:
        kw["pin_main"] = req.pin_main
    if req.pin_purge is not None:
        kw["pin_purge"] = req.pin_purge
    return kw


# ── 接口 ────────────────────────────────────────────────


@app.get("/api/health")
def health():
    return {"ok": True, "version": app.version}


@app.post("/api/compile")
def api_compile(req: CompileReq):
    try:
        d = make_dialect(req.dialect, **_dialect_kw(req))
    except ValueError as e:
        raise HTTPException(400, str(e))

    try:
        c = compile_project(req.project, d, safe_z=req.safe_z)
    except Exception as e:
        raise HTTPException(500, f"compile failed: {e}")

    try:
        sim = simulate(c.text, accel=req.project.machine.accel)
        tr = find_io_transitions(sim)
    except Exception as e:
        sim = None
        tr = []
        c.warnings.append(f"simulate failed: {e}")

    return {
        "gcode": c.text,
        "stats": {
            "strokes": c.stroke_count,
            "lines": len(c.lines),
            "path_length": round(c.path_length, 2),
            "est_seconds": round(estimate_time(c.lines, req.project.machine, req.project.process), 1),
            # 上位机延时总和（dwell="host" 模式才有）
            "host_dwell_seconds": round(sum(c.delays), 2),
        },
        "warnings": c.warnings,
        "sim": sim.to_dict() if sim else None,
        "io_transitions": tr,
        # dwell="host" 时，下发器要按这个数组 sleep
        "delays": [round(x, 4) for x in c.delays],
        "dialect": {
            "name": d.name,
            "queued_io": d.queued_io,
            "max_line_len": d.max_line_len,
            "dwell": c.dwell_mode,
            "dwell_unit": getattr(d.caps, "dwell_unit", "ms"),
            "io_channels": getattr(d.caps, "io_channels", 8),
        },
        # 迁移后的工具列表（旧工程在这里被归并成工具）
        "tools": [
            {
                "id": t.id, "name": t.name, "color": t.color,
                "enabled": t.enabled, "kind": t.kind,
                "on_channel": t.on_channel, "rise_ms": t.rise_ms,
                "off_channel": t.close_channel(), "tail_ms": t.tail_ms,
            }
            for t in req.project.tools
        ],
        # IR 统计：让界面能显示「生成了多少条指令」
        "ir": {
            "count": len(c.program.instrs) if c.program else 0,
            "dwell_ms": c.program.dwell_total_ms() if c.program else 0,
            "tools_used": c.program.tool_used if c.program else [],
        },
    }


@app.post("/api/fill")
def api_fill(req: FillReq):
    pts = discretize_source(req.source)
    if not pts:
        return {"paths": [], "count": 0}

    paths = _run_fill(pts, req.pattern, req.spacing, req.angle, req.inset, req.min_len,
                      req.stagger, req.outline)

    if req.merge:
        paths = merge_paths(paths, tol=0.05)
    if req.sort:
        paths = sort_paths(paths)

    return {
        "paths": [[[round(x, 4), round(y, 4)] for x, y in p] for p in paths],
        "count": len(paths),
        "bbox": bbox_of(paths),
        "pattern": req.pattern,
    }


def _run_fill(
    pts: list[tuple[float, float]],
    pattern: str,
    spacing: float,
    angle: float,
    inset: float,
    min_len: float,
    stagger: bool = False,
    outline: bool = True,
) -> list[list[tuple[float, float]]]:
    """按图案类型调度填充。格栅/蜂窝不走 Shapely 的扫描线。"""
    from .infill import fill_grid, fill_honeycomb

    if pattern == "grid":
        return fill_grid(pts, spacing=spacing, inset=inset, min_len=min_len,
                         stagger=stagger, outline=outline)
    if pattern == "honeycomb":
        return fill_honeycomb(pts, spacing=spacing, inset=inset, min_len=min_len,
                              outline=outline)
    if pattern == "concentric":
        return fill_concentric(pts, step=spacing, min_len=min_len)
    return fill_scanline(pts, spacing=spacing, angle_deg=angle, inset=inset,
                         min_len=min_len)


@app.post("/api/discretize")
def api_discretize(req: DiscretizeReq):
    pts = discretize_source(req.source)
    return {
        "points": [[round(x, 4), round(y, 4)] for x, y in pts],
        "count": len(pts),
    }


@app.post("/api/svg-import")
def api_svg_import(req: SvgImportReq):
    """把 SVG 文本解析成笔画列表（不直接进工程，前端决定要不要加）。"""
    from .svg_import import parse_svg

    r = parse_svg(req.svg, tol=req.tol, flip_y=req.flip_y)
    subs = r.subpaths

    if req.scale != 1.0:
        subs = [[(x * req.scale, y * req.scale) for x, y in sp] for sp in subs]

    if req.only_closed:
        subs = [
            sp for sp in subs
            if len(sp) >= 3 and (sp[0][0] - sp[-1][0]) ** 2 + (sp[0][1] - sp[-1][1]) ** 2 < 1e-4
        ]

    truncated = False
    if len(subs) > req.max_paths:
        subs = subs[: req.max_paths]
        truncated = True

    warnings = list(r.warnings)
    if truncated:
        warnings.append(f"路径太多，只取了前 {req.max_paths} 条")

    return {
        "paths": [[[round(x, 4), round(y, 4)] for x, y in sp] for sp in subs],
        "count": len(subs),
        "size": {"width": round(r.width, 2), "height": round(r.height, 2)},
        "bbox": bbox_of(subs) if subs else None,
        "warnings": warnings,
        "unsupported": r.unsupported,
    }


@app.post("/api/mesh-slice")
def api_mesh_slice(req: MeshSliceReq):
    """STL/OBJ → 逐层轮廓。返回的 layers 可直接塞进工程。"""
    import base64

    from .mesh import load_mesh, mesh_bounds, slice_layers

    try:
        raw = base64.b64decode(req.data_b64, validate=False)
    except Exception as e:
        raise HTTPException(400, f"base64 解码失败: {e}")

    if len(raw) > 80 * 1024 * 1024:
        raise HTTPException(400, "文件太大（上限 80MB）")

    tris, warns = load_mesh(req.filename, raw)
    if not tris:
        return {
            "layers": [],
            "count": 0,
            "warnings": warns or ["没有解析到三角形"],
            "bounds": None,
        }

    if req.scale != 1.0:
        from .mesh import transform_mesh

        tris = transform_mesh(tris, scale=req.scale)

    b = mesh_bounds(tris)
    r = slice_layers(tris, layer_height=req.layer_height, max_layers=req.max_layers)
    warns.extend(r.warnings)

    out_layers = []
    for z, loops in r.layers:
        if req.only_largest and len(loops) > 1:
            loops = [max(loops, key=lambda l: abs(_poly_area(l)))]
        # 平移到摆放位置
        loops = [[(x + req.offset_x, y + req.offset_y) for x, y in lp] for lp in loops]
        out_layers.append(
            {
                "z": z,
                "paths": [[[round(x, 4), round(y, 4)] for x, y in lp] for lp in loops],
            }
        )

    return {
        "layers": out_layers,
        "count": len(out_layers),
        "warnings": warns,
        "bounds": {
            "min": [round(v, 3) for v in b[:3]],
            "max": [round(v, 3) for v in b[3:]],
            "size": [round(b[3] - b[0], 3), round(b[4] - b[1], 3), round(b[5] - b[2], 3)],
        },
        "stats": {
            "triangles": len(tris),
            "layers": len(out_layers),
            "paths": sum(len(l["paths"]) for l in out_layers),
        },
    }


def _poly_area(pts) -> float:
    """鞋带公式，返回有符号面积。"""
    a = 0.0
    n = len(pts)
    for i in range(n):
        x1, y1 = pts[i]
        x2, y2 = pts[(i + 1) % n]
        a += x1 * y2 - x2 * y1
    return a / 2.0


@app.post("/api/fill-all")
def api_fill_all(req: FillAllReq):
    """一键全层填充。

    对工程里每一层，把「轮廓」类笔画逐个填充，生成的填充线加进同一层。

    注意：层可能带 repeat（重复打印 N 次），但几何是同一份，
    所以只对图层定义填一次，repeat 会在编译时自然重复。
    """
    proj = req.project
    total_added = 0
    layers_done = 0
    layers_skipped = 0
    warnings: list[str] = []
    rise = proj.process.rise_ms
    tail = proj.process.tail_ms

    for layer in proj.layers:
        if not layer.visible:
            continue

        contours = [s for s in layer.strokes if s.kind != "fill"]
        has_fill = any(s.kind == "fill" for s in layer.strokes)

        # replace_existing 优先：要替换就先清掉旧填充，不能被 skip_filled 拦住
        if has_fill and req.replace_existing:
            layer.strokes = [s for s in layer.strokes if s.kind != "fill"]
            has_fill = False

        if req.skip_filled and has_fill:
            layers_skipped += 1
            continue
        if not contours:
            continue

        added_this_layer = 0
        for st in contours:
            # 优先用图元描述；没有就退化成用点序列建多边形
            if st.source is not None:
                pts = discretize_source(st.source)
            else:
                pts = [(p.x, p.y) for p in st.points]
            if len(pts) < 3:
                continue

            paths = _run_fill(pts, req.pattern, req.spacing, req.angle, req.inset,
                              req.min_len, req.stagger, req.outline)
            if not paths:
                continue
            paths = sort_paths(paths)

            for p in paths:
                layer.strokes.append(
                    Stroke(
                        id=f"fa_{layer.id}_{added_this_layer}",
                        points=to_points(p),
                        closed=False,
                        kind="fill",
                        start=IOEvent(io=st.start.io, level=st.start.level, delay_ms=rise),
                        end=IOEvent(io=st.end.io, level=st.end.level, delay_ms=tail),
                    )
                )
                added_this_layer += 1

        if added_this_layer:
            layers_done += 1
            total_added += added_this_layer
        else:
            warnings.append(f"层 {layer.id}（Z={layer.z}）填充为空，可能内缩太大")

    if not total_added:
        if layers_skipped and layers_skipped == len([l for l in proj.layers if l.visible]):
            warnings.insert(
                0,
                f"所有 {layers_skipped} 个图层都已有填充线，已跳过。"
                "如果想重新填充，先删掉旧的填充线，或改用「替换已有填充」。",
            )
        else:
            warnings.insert(
                0,
                "没有新增任何填充线。可能是：轮廓太小被内缩吃掉了"
                "（把「离边距离」调小），或图层里只有填充线没有轮廓。",
            )

    return {
        "project": json.loads(proj.model_dump_json()),
        "added": total_added,
        "layers_done": layers_done,
        "layers_skipped": layers_skipped,
        "layers_total": len(proj.layers),
        "warnings": warnings,
    }


@app.get("/api/sample-project")
def api_sample():
    """返回一个多层格栅块示例工程，前端「新建」时用。

    做成 repeat 多层，这样 3D 预览里能看出立体效果。
    """
    size, cells = 8.0, 3
    strokes: list[Stroke] = [
        Stroke(
            id="outline",
            points=[Point(x=0, y=0), Point(x=size, y=0), Point(x=size, y=size), Point(x=0, y=size), Point(x=0, y=0)],
            closed=True,
            kind="contour",
            source=Source(type="rect", x=0, y=0, w=size, h=size),
        )
    ]
    step = size / cells
    for i in range(1, cells):
        strokes.append(
            Stroke(id=f"v{i}", points=[Point(x=i * step, y=0), Point(x=i * step, y=size)], kind="fill")
        )
        strokes.append(
            Stroke(id=f"h{i}", points=[Point(x=0, y=i * step), Point(x=size, y=i * step)], kind="fill")
        )
    # 顶面密封横线
    for i in range(4):
        yy = size * (i + 0.5) / 4
        strokes.append(
            Stroke(id=f"cap{i}", points=[Point(x=0, y=yy), Point(x=size, y=yy)], kind="fill")
        )

    proj = Project(
        name="格栅块示例",
        process=Process(rise_ms=120, tail_ms=80, lift_z=0.3, line_width=0.45, layer_height=0.4),
        layers=[Layer(id="L1", z=0.4, repeat=6, z_step=0.4, strokes=strokes)],
    )
    return json.loads(proj.model_dump_json())


# ── 静态文件 ────────────────────────────────────────────


@app.get("/manual")
def manual():
    """使用手册（markdown 渲染成 HTML）。"""
    md = ROOT / "docs" / "使用手册.md"
    if not md.exists():
        return PlainTextResponse("手册还没生成", 404)
    html = _render_markdown(md.read_text(encoding="utf-8"))
    return PlainTextResponse(html, media_type="text/html; charset=utf-8")


def _render_markdown(text: str) -> str:
    """极简 Markdown → HTML。只支持手册里用到的那几种语法。

    不引第三方库，避免增加依赖。支持：# 标题、表格、代码块、
    列表、图片、链接、粗体、行内代码、分割线。
    """
    import html as _html
    import re

    lines = text.split("\n")
    out: list[str] = []
    in_code = False
    in_table = False
    table_rows: list[str] = []

    def flush_table():
        nonlocal in_table, table_rows
        if not table_rows:
            return
        out.append('<table>')
        for i, row in enumerate(table_rows):
            cells = [c.strip() for c in row.strip().strip("|").split("|")]
            if i == 1 and all(set(c) <= set("-: ") for c in cells):
                continue  # 分隔行
            tag = "th" if i == 0 else "td"
            # 单元格内容也要走 inline()，否则 **粗体** 和 `代码` 不生效
            out.append("<tr>" + "".join(f"<{tag}>{inline(c)}</{tag}>" for c in cells) + "</tr>")
        out.append("</table>")
        table_rows = []
        in_table = False

    def inline(s: str) -> str:
        s = _html.escape(s)
        s = re.sub(r"`([^`]+)`", r"<code>\1</code>", s)
        s = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", s)
        s = re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", r'<img alt="\1" src="/manual-img/\2">', s)
        s = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r'<a href="\2">\1</a>', s)
        return s

    for raw in lines:
        line = raw.rstrip()

        if line.startswith("```"):
            if in_code:
                out.append("</code></pre>")
                in_code = False
            else:
                flush_table()
                out.append("<pre><code>")
                in_code = True
            continue
        if in_code:
            out.append(_html.escape(line))
            continue

        if line.strip().startswith("|") and line.strip().endswith("|"):
            if not in_table:
                in_table = True
                table_rows = []
            table_rows.append(line)
            continue
        elif in_table:
            flush_table()

        if not line.strip():
            out.append("")
            continue

        m = re.match(r"^(#{1,4})\s+(.*)$", line)
        if m:
            lvl = len(m.group(1))
            out.append(f"<h{lvl}>{inline(m.group(2))}</h{lvl}>")
            continue

        if line.strip() in ("---", "***", "___"):
            out.append("<hr>")
            continue

        if re.match(r"^\s*[-*]\s+", line):
            out.append(f"<li>{inline(re.sub(r'^\s*[-*]\s+', '', line))}</li>")
            continue
        if re.match(r"^\s*\d+\.\s+", line):
            out.append(f"<li>{inline(re.sub(r'^\s*\d+\.\s+', '', line))}</li>")
            continue

        if line.startswith(">"):
            out.append(f"<blockquote>{inline(line.lstrip('> '))}</blockquote>")
            continue

        out.append(f"<p>{inline(line)}</p>")

    if in_code:
        out.append("</code></pre>")
    flush_table()

    body = "\n".join(out)
    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>{APP_TITLE} · 使用手册</title>
<style>
body{{max-width:860px;margin:0 auto;padding:36px 28px 90px;
background:#14171c;color:#dfe4ec;
font:15px/1.75 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif}}
h1{{font-size:28px;margin:34px 0 18px;padding-bottom:10px;border-bottom:1px solid #2e3540}}
h2{{font-size:21px;margin:38px 0 14px;color:#4ea3ff}}
h3{{font-size:16.5px;margin:26px 0 10px;color:#9dc8ff}}
h4{{font-size:14.5px;margin:20px 0 8px;color:#8b95a5}}
p{{margin:11px 0}}
img{{max-width:100%;border-radius:8px;border:1px solid #2e3540;margin:14px 0;display:block}}
code{{background:#22272f;padding:2px 6px;border-radius:4px;font-size:13px;
font-family:"Cascadia Code",Consolas,monospace;color:#a8d8b0}}
pre{{background:#0d1117;border:1px solid #2e3540;border-radius:8px;
padding:14px 16px;overflow-x:auto;margin:14px 0}}
pre code{{background:none;padding:0;color:#b8c4d4;font-size:12.5px;line-height:1.65}}
table{{border-collapse:collapse;width:100%;margin:16px 0;font-size:13.5px}}
th,td{{border:1px solid #2e3540;padding:8px 11px;text-align:left}}
th{{background:#1f242c;font-weight:600;color:#9dc8ff}}
tr:nth-child(even) td{{background:#181c22}}
li{{margin:5px 0 5px 22px}}
hr{{border:none;border-top:1px solid #2e3540;margin:32px 0}}
blockquote{{border-left:3px solid #4ea3ff;background:#1a1f26;
margin:14px 0;padding:10px 16px;color:#a8b4c4;border-radius:0 6px 6px 0}}
a{{color:#4ea3ff}}
.top{{position:fixed;top:0;left:0;right:0;height:44px;background:#171b21;
border-bottom:1px solid #2e3540;display:flex;align-items:center;
padding:0 20px;gap:14px;font-size:13px;z-index:9}}
.top a{{color:#8b95a5;text-decoration:none}}
.top a:hover{{color:#4ea3ff}}
.pad{{height:44px}}
</style></head><body>
<div class="top">
  <b style="color:#4ea3ff">{APP_NAME}</b>
  <a href="/">← 回到编辑器</a>
  <span style="color:#5a6373">使用手册</span>
</div>
<div class="pad"></div>
{body}
</body></html>"""


@app.get("/manual-img/{name:path}")
def manual_img(name: str):
    """手册里的截图。支持子目录（如 f1/01-serial-session.png）。

    只允许 _manual/ 下的文件，防目录穿越。
    """
    base = (ROOT / "_manual").resolve()
    p = (base / name).resolve()
    if not str(p).startswith(str(base)) or not p.exists():
        raise HTTPException(404, "image not found")
    return FileResponse(p)


@app.get("/api/version")
def api_version() -> dict:
    """版本信息。前端状态栏和「关于」用它。"""
    return {"version": VERSION, "app": APP_NAME, "slug": APP_SLUG,
            "desc": APP_DESC, "legacy": list(LEGACY_NAMES)}


@app.get("/docs/{name}")
def docs_page(name: str):
    """把 docs/ 下的 md 渲染成网页（如 F1板卡测试报告）。"""
    md = ROOT / "docs" / f"{name}.md"
    if not md.exists():
        raise HTTPException(404, "doc not found")
    html = _render_markdown(md.read_text(encoding="utf-8"))
    return PlainTextResponse(html, media_type="text/html; charset=utf-8")


@app.get("/api/serial/ports")
def api_serial_ports():
    """列出可用串口。"""
    from .serial_f1 import list_serial_ports

    return {"ports": list_serial_ports()}


class SerialConnectReq(BaseModel):
    port: str = Field("COM7")
    baud: int = Field(115200, gt=0)


@app.post("/api/serial/connect")
def api_serial_connect(req: SerialConnectReq):
    from .serial_f1 import get_serial_link

    link = get_serial_link(req.port, req.baud)
    ok, msg = link.open()
    return {"ok": ok, "message": msg, "state": link.snapshot()}


@app.post("/api/serial/disconnect")
def api_serial_disconnect(req: SerialConnectReq):
    from .serial_f1 import get_serial_link

    link = get_serial_link(req.port, req.baud)
    link.close()
    return {"ok": True, "message": "已断开", "state": link.snapshot()}


@app.get("/api/serial/status")
def api_serial_status(port: str = "COM7", baud: int = 115200):
    from .serial_f1 import get_serial_link

    return get_serial_link(port, baud).snapshot()


@app.post("/api/serial/probe")
def api_serial_probe(req: SerialConnectReq):
    """探活：发 M50。"""
    from .serial_f1 import get_serial_link

    link = get_serial_link(req.port, req.baud)
    if not link.state.connected:
        ok, msg = link.open()
        if not ok:
            return {"ok": False, "message": msg}
    r = link.query_status()
    if r.is_status:
        return {
            "ok": True,
            "message": f"连通，位置 ({r.pos[0]:g}, {r.pos[1]:g}, {r.pos[2]:g})",
            "pos": list(r.pos),
            "limits_left": list(r.limits_left),
            "limits_right": list(r.limits_right),
        }
    return {"ok": False, "message": f"M50 返回 {r.code}（{r.desc}）" if r.code else "无应答"}


@app.post("/api/serial/send-one")
def api_serial_send_one(payload: dict):
    """手动发一行。"""
    from .serial_f1 import get_serial_link

    port = payload.get("port", "COM7")
    baud = int(payload.get("baud", 115200))
    line = str(payload.get("line", "")).strip()
    if not line:
        raise HTTPException(400, "指令为空")

    link = get_serial_link(port, baud)
    if not link.state.connected:
        ok, msg = link.open()
        if not ok:
            return {"ok": False, "message": msg}

    r = link.ask(line, timeout=120 if line.upper().startswith(("G00", "G01", "G02", "G28")) else 10)
    return {
        "ok": r.ok,
        "code": r.code,
        "raw": r.raw,
        "desc": r.desc,
        "elapsed": round(r.elapsed, 3),
        "message": r.raw or "(超时)",
    }


class SerialJobReq(BaseModel):
    gcode: str
    port: str = Field("COM7")
    baud: int = Field(115200, gt=0)


@app.post("/api/serial/job/start")
def api_serial_job_start(req: SerialJobReq):
    from .serial_f1 import get_serial_job, get_serial_link

    link = get_serial_link(req.port, req.baud)
    if not link.state.connected:
        ok, msg = link.open()
        if not ok:
            return {"ok": False, "message": msg}

    job = get_serial_job(req.port, req.baud)
    ok, msg = job.start(req.gcode)
    return {"ok": ok, "message": msg, "state": job.snapshot()}


@app.post("/api/serial/job/stop")
def api_serial_job_stop(req: SerialConnectReq):
    from .serial_f1 import get_serial_job

    job = get_serial_job(req.port, req.baud)
    job.stop()
    return {"ok": True, "message": "已请求中止", "state": job.snapshot()}


@app.get("/api/serial/job/status")
def api_serial_job_status(port: str = "COM7", baud: int = 115200):
    from .serial_f1 import get_serial_job

    return get_serial_job(port, baud).snapshot()


@app.get("/api/logs")
def api_logs(after: int = 0, limit: int = 800):
    """增量拉取日志。after = 前端已收到的最后一条 seq。"""
    from .logger import get_logger

    return get_logger().since(after, limit)


@app.post("/api/logs/clear")
def api_logs_clear():
    from .logger import get_logger

    get_logger().clear()
    return {"ok": True}


@app.get("/api/logs/info")
def api_logs_info():
    from .logger import get_logger

    lg = get_logger()
    return {
        "file_enabled": lg.file_enabled,
        "file": str(lg._file_path) if getattr(lg, "_file_path", None) else None,
        "dir": str(getattr(lg, "_log_dir", "") or ""),
        "console": lg.console_enabled,
    }


@app.get("/")
def index():
    f = WEB / "index.html"
    if not f.exists():
        return PlainTextResponse("web/index.html not found", 404)
    return FileResponse(f)


# ══════════════════════════════════════════════════════════
# 连接 / 下发
# ══════════════════════════════════════════════════════════


class ConnectReq(BaseModel):
    ip: str = Field("192.168.0.102")
    port: int = Field(5000, gt=0, lt=65536)


class JobReq(BaseModel):
    gcode: str
    ip: str = Field("192.168.0.102")
    port: int = Field(5000, gt=0, lt=65536)
    # dwell="host" 模式下，每行之后上位机要等多少秒（来自 /api/compile）
    delays: list[float] | None = None
    # 每包发几条。1=逐行一发一等（每行约 300ms 往返开销）
    # 4~6 能把开销摊薄到 50~75ms/行。0/None = 用默认
    batch_size: int = Field(6, ge=0, le=8)
    # 目标方言。决定 G4 的 P 单位（H5 秒 / 其余毫秒）等，
    # 影响下发器等待超时的计算。
    dialect: str = Field("h5")


@app.get("/api/link/mock/status")
def mock_status():
    from .link import get_mock

    return get_mock().snapshot()


@app.post("/api/link/mock/start")
def mock_start(speed: float = 1.0):
    """启动模拟板。speed>1 时运动时间按比例缩短，方便快速验证长任务。"""
    from .link import get_mock

    m = get_mock()
    m.state.speed_scale = max(0.1, min(1000.0, speed))
    ok, msg = m.start()
    return {"ok": ok, "message": msg + f"（速度 ×{m.state.speed_scale:g}）", "state": m.snapshot()}


@app.post("/api/link/mock/stop")
def mock_stop():
    from .link import get_mock

    ok, msg = get_mock().stop()
    return {"ok": ok, "message": msg}


@app.post("/api/link/connect")
def link_connect(req: ConnectReq):
    from .link import get_link

    link = get_link(req.ip, req.port)
    ok, msg = link.open()
    return {"ok": ok, "message": msg, "state": link.snapshot()}


@app.post("/api/link/disconnect")
def link_disconnect(req: ConnectReq):
    from .link import get_link

    link = get_link(req.ip, req.port)
    link.close()
    return {"ok": True, "message": "已断开", "state": link.snapshot()}


@app.post("/api/link/probe")
def link_probe(req: ConnectReq):
    """探活：发 M114 看有没有回包。没连接时先自动连。"""
    from .link import get_link

    link = get_link(req.ip, req.port)
    if not link.sock:
        link.open()
    code, detail = link.ask("M114", timeout=2.0)
    return {
        "ok": code == "E000",
        "code": code,
        "detail": detail,
        "message": "连通" if code == "E000" else (f"返回 {code}" if code else "无回包"),
    }


@app.post("/api/link/send-one")
def link_send_one(payload: dict):
    """手动发一行，调试用。"""
    from .link import get_link

    ip = payload.get("ip", "192.168.0.102")
    port = int(payload.get("port", 5000))
    line = str(payload.get("line", "")).strip()
    if not line:
        raise HTTPException(400, "指令为空")
    link = get_link(ip, port)
    if not link.sock:
        ok, msg = link.open()
        if not ok:
            return {"ok": False, "message": msg}
    code, detail = link.ask(line, timeout=10.0)
    return {
        "ok": code in ("E000", "B000"),
        "code": code,
        "detail": detail,
        "message": f"{code} {detail}".strip() if code else "无回包（超时）",
    }


@app.post("/api/job/start")
def job_start(req: JobReq):
    from .link import get_job, get_link

    link = get_link(req.ip, req.port)
    if not link.sock:
        ok, msg = link.open()
        if not ok:
            return {"ok": False, "message": msg}
    job = get_job(req.ip, req.port, batch_size=req.batch_size or 1)
    caps = DIALECTS.get(req.dialect)
    dwell_unit = caps.dwell_unit if caps else "ms"
    ok, msg = job.start(req.gcode, delays=req.delays, dwell_unit=dwell_unit)
    return {"ok": ok, "message": msg, "state": job.snapshot()}


@app.post("/api/job/stop")
def job_stop(req: ConnectReq):
    from .link import get_job

    job = get_job(req.ip, req.port)
    job.stop()
    return {"ok": True, "message": "已请求中止", "state": job.snapshot()}


@app.get("/api/job/status")
def job_status(ip: str = "192.168.0.102", port: int = 5000):
    from .link import get_job

    return get_job(ip, port).snapshot()


if WEB.exists():
    app.mount("/static", StaticFiles(directory=str(WEB)), name="static")
