"""笔画 → G-code 编译器。

一条笔画展开成（见 docs/00 §4）：

    确保阀关 → 空驶到起点 → 降 Z → 开阀 → 驻留
    → G1 各点（恒速）→ G1 到终点前 Δ → 关阀 → 驻留 → 抬针

Δ = feed × tail_ms/1000 —— 把「延时」换算成「距离」，
这样改速度时几何不用重算。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .dialects import Ctx, Dialect, _f
from .model import IOEvent, Layer, Machine, Process, Project, Stroke


@dataclass
class Compiled:
    text: str
    lines: list[str]
    stroke_count: int = 0
    path_length: float = 0.0
    warnings: list[str] = field(default_factory=list)


def _dist(a, b) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _backtrack(
    pts: list[tuple[float, float]], back: float
) -> tuple[tuple[float, float], int]:
    """沿折线从终点往回退 back 距离。

    返回 (切点坐标, 切点所在段的起点索引)。
    索引含义：pts[0..i] 是关阀前要走完的部分，切点在第 i 段内部（或正好是 pts[i]）。

    如果 back 超过整条路径长度，就把切点钳到起点（路径太短，
    这种情况应该由 min_segment_len 校验提前警告）。
    """
    if back <= 1e-9:
        return pts[-1], len(pts) - 1

    remaining = back
    for i in range(len(pts) - 1, 0, -1):
        seg = _dist(pts[i - 1], pts[i])
        if seg <= 1e-12:
            continue
        if remaining < seg:
            t = remaining / seg
            cut = (
                pts[i][0] + (pts[i - 1][0] - pts[i][0]) * t,
                pts[i][1] + (pts[i - 1][1] - pts[i][1]) * t,
            )
            return cut, i
        remaining -= seg

    # 退过头：钳到起点
    return pts[0], 0


def _effective(process: Process, layer: Layer, stroke: Stroke) -> dict:
    """合并 process / layer.overrides / stroke 的生效参数。"""
    eff = {
        "rise_ms": process.rise_ms,
        "tail_ms": process.tail_ms,
        "lift_z": process.lift_z,
        "lift_feed": process.lift_feed,
        "feed": process.min_segment_len and 0 or 0,  # 占位，下面覆盖
    }
    for k in ("rise_ms", "tail_ms", "lift_z", "lift_feed"):
        if k in layer.overrides:
            eff[k] = layer.overrides[k]
    if stroke.feed:
        eff["feed"] = stroke.feed
    return eff


def compile_project(
    project: Project,
    dialect: Dialect,
    *,
    safe_z: float | None = None,
    emit_ensure_closed: bool = True,
) -> Compiled:
    """把整个工程编译成 G-code 文本。"""
    m = project.machine
    p = project.process
    out: list[str] = []
    warnings: list[str] = []
    stroke_n = 0
    total_len = 0.0

    ctx = Ctx(machine=m, process=p, z=0.0)
    out.extend(dialect.header(ctx))

    lift = p.lift_z
    safe = safe_z if safe_z is not None else max(m.travel_z, lift + 1.0)

    # 先抬到安全高度
    out.append(f"; lift to safe z={_f(safe)}")
    out.extend(dialect.move_z(ctx, safe, p.lift_feed))

    for layer in project.layers:
        if not layer.visible or not layer.strokes:
            continue
        for rep in range(layer.repeat):
            z = layer.z + rep * layer.z_step
            out.append("")
            out.append(f"; === layer {layer.id} rep {rep + 1}/{layer.repeat} z={_f(z)} ===")
            for st in layer.strokes:
                eff = _effective(p, layer, st)
                feed = st.feed or m.feed_default
                rise = int(eff["rise_ms"])
                tail = int(eff["tail_ms"])
                lz = float(eff["lift_z"])
                lf = float(eff["lift_feed"])

                pts = [(q.x, q.y) for q in st.points]
                if len(pts) < 2:
                    warnings.append(f"stroke {st.id}: 点数不足 2，跳过")
                    continue

                # 软限位检查
                for (x, y) in pts:
                    if abs(x) > m.travel_x or abs(y) > m.travel_y:
                        warnings.append(
                            f"stroke {st.id}: 点 ({_f(x)},{_f(y)}) 超出行程 "
                            f"{_f(m.travel_x)}x{_f(m.travel_y)}"
                        )
                        break

                ctx.z = z
                stroke_n += 1
                seg_len = sum(_dist(pts[i], pts[i + 1]) for i in range(len(pts) - 1))
                total_len += seg_len

                out.append("")
                out.append(f"; --- stroke {st.id} ({st.kind}) len={_f(seg_len, 2)}mm ---")

                # ① 确保阀关（幂等，防上一条异常遗留）
                if emit_ensure_closed:
                    out.extend(dialect.ensure_closed(ctx, st.start.io, 0))

                # ② 空驶到起点（阀关着，可快速）
                out.extend(dialect.travel(ctx, pts[0][0], pts[0][1], m.feed_rapid))

                # ③ 降到位
                out.extend(dialect.move_z(ctx, z, lf))

                # ④ 开阀
                out.extend(dialect.valve(ctx, IOEvent(io=st.start.io, level=st.start.level, delay_ms=0)))

                # ⑤ 上升延时
                out.extend(dialect.dwell(ctx, rise))

                # ⑥ 打印段
                # Δ = v(mm/s) × t(s) = (feed/60) × (tail/1000)
                back = (feed / 60.0) * (tail / 1000.0)
                end = pts[-1]

                if dialect.queued_io:
                    # ── IO 随运动排队（Klipper）：不需要切一刀 ──
                    # 关阀会精确落在最后一条 G1 的结束时刻，机器全程不停，
                    # 几何完全按原路径走，不产生额外的减速-加速。
                    for i in range(1, len(pts)):
                        out.extend(dialect.print_move(ctx, pts[i][0], pts[i][1], feed))
                    out.extend(dialect.valve(ctx, IOEvent(io=st.end.io, level=st.end.level, delay_ms=0)))
                    out.extend(dialect.dwell(ctx, tail))
                elif back > 1e-6:
                    # ── IO 不排队（Marlin/自研板）：终点前切一刀停车关阀 ──
                    cut, cut_idx = _backtrack(pts, back)
                    if cut_idx == 0:
                        warnings.append(
                            f"stroke {st.id}: 路径总长 {_f(seg_len, 2)}mm < 回退距离 "
                            f"{_f(back, 3)}mm，切点钳到起点"
                        )
                    # 走到切点之前的所有点
                    for i in range(1, cut_idx):
                        out.extend(dialect.print_move(ctx, pts[i][0], pts[i][1], feed))
                    out.extend(dialect.print_move(ctx, cut[0], cut[1], feed))
                    # ⑦ 关阀（此时停在 cut 点）
                    out.extend(dialect.valve(ctx, IOEvent(io=st.end.io, level=st.end.level, delay_ms=0)))
                    # ⑧ 拖尾驻留
                    out.extend(dialect.dwell(ctx, tail))
                    # ⑨ 走完剩余 Δ（若切点不是起点，终点必然还没走到）
                    out.extend(dialect.print_move(ctx, end[0], end[1], feed))
                else:
                    for i in range(1, len(pts)):
                        out.extend(dialect.print_move(ctx, pts[i][0], pts[i][1], feed))
                    out.extend(dialect.valve(ctx, IOEvent(io=st.end.io, level=st.end.level, delay_ms=0)))
                    out.extend(dialect.dwell(ctx, tail))

                # ⑩ 抬针断料
                out.extend(dialect.move_z(ctx, z + lz, lf))

    out.append("")
    out.extend(dialect.footer(ctx))

    # 行长度校验
    if dialect.max_line_len:
        for ln in out:
            if len(ln) > dialect.max_line_len:
                warnings.append(f"行超长 {len(ln)}B > {dialect.max_line_len}B: {ln[:40]}...")

    return Compiled(
        text="\n".join(out) + "\n",
        lines=out,
        stroke_count=stroke_n,
        path_length=total_len,
        warnings=warnings,
    )


def estimate_time(lines: list[str], machine: Machine, process: Process) -> float:
    """粗略估算总时长（秒），给用户一个心理预期。"""
    t = 0.0
    x = y = z = 0.0
    feed = machine.feed_default
    for ln in lines:
        s = ln.strip()
        if not s or s.startswith(";"):
            continue
        up = s.upper()
        if "G4" in up and "P" in up:
            try:
                p = up.split("P")[1].split()[0]
                t += float(p) / 1000.0
            except Exception:
                pass
            continue
        if not (up.startswith("G0") or up.startswith("G1")):
            continue
        if "F" in up:
            try:
                feed = float(up.split("F")[1].split()[0])
            except Exception:
                pass
        nx, ny, nz = x, y, z
        for tok in up.split():
            try:
                if tok.startswith("X"):
                    nx = float(tok[1:])
                elif tok.startswith("Y"):
                    ny = float(tok[1:])
                elif tok.startswith("Z"):
                    nz = float(tok[1:])
            except ValueError:
                pass
        d = math.sqrt((nx - x) ** 2 + (ny - y) ** 2 + (nz - z) ** 2)
        if d > 0 and feed > 0:
            t += d / (feed / 60.0)
        x, y, z = nx, ny, nz
    return t
