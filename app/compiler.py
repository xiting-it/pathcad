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
    # 每行之后上位机要等多少秒。
    # 只有 dialect.caps.dwell == "host" 时才有非零值；
    # "gcode" 模式延时已写进 text，"none" 模式直接丢弃。
    delays: list[float] = field(default_factory=list)
    dwell_mode: str = "gcode"

    def __post_init__(self):
        if not self.delays:
            self.delays = [0.0] * len(self.lines)


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
    """把整个工程编译成 G-code 文本。

    延时有两种落法（见 model.DialectCaps.dwell）：
      - "gcode"：dialect.dwell() 会返回 G4 行，延时就在文本里
      - "host" ：dialect.dwell() 返回空，延时要记在 delays[] 里，
                 由下发器在「等 E000 之后」sleep
      - "none" ：延时直接丢弃
    """
    m = project.machine
    p = project.process
    out: list[str] = []
    delays: list[float] = []
    warnings: list[str] = []
    stroke_n = 0
    total_len = 0.0

    dwell_mode = getattr(getattr(dialect, "caps", None), "dwell", None) or "gcode"

    # IO 状态跟踪：只在电平真的变化时才发指令。
    # 网口板每条指令约 200ms 开销，而每条笔画开头一次的 M121 U0 V0
    # 大部分是冗余的（上一条笔画结束时已经关过），去掉能省 10~15% 指令数。
    io_level: dict[int, int] = {}

    def emit(lines: list[str], delay_s: float = 0.0):
        """追加若干行；把这条延时挂到最后一行上（host 模式用）。"""
        if not lines:
            return
        out.extend(lines)
        for i in range(len(lines)):
            delays.append(delay_s if i == len(lines) - 1 else 0.0)

    def emit_valve(io: int, level: int, force: bool = False):
        """开/关阀，带状态去重。"""
        if not force and io_level.get(io, 0) == level:
            return
        emit(dialect.valve(ctx, IOEvent(io=io, level=level, delay_ms=0)))
        io_level[io] = level

    def emit_dwell(ms: int):
        """按方言能力处理延时。"""
        if ms <= 0:
            return
        if dwell_mode == "gcode":
            # 方言自己生成 G4 行
            emit(dialect.dwell(ctx, ms))
        elif dwell_mode == "host":
            # 不生成 G4，延时交给下发器；挂到当前最后一行上
            if delays:
                delays[-1] += ms / 1000.0
            else:
                # 还没有任何行，先塞个空行承载延时
                emit(["; dwell"], ms / 1000.0)
        # none 模式：丢弃

    ctx = Ctx(machine=m, process=p, z=0.0)
    emit(dialect.header(ctx))

    lift = p.lift_z
    safe = safe_z if safe_z is not None else max(m.travel_z, lift + 1.0)

    # 先抬到安全高度
    emit([f"; lift to safe z={_f(safe)}"])
    emit(dialect.move_z(ctx, safe, p.lift_feed))

    for layer in project.layers:
        if not layer.visible or not layer.strokes:
            continue
        for rep in range(layer.repeat):
            z = layer.z + rep * layer.z_step
            emit([""])
            emit([f"; === layer {layer.id} rep {rep + 1}/{layer.repeat} z={_f(z)} ==="])
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

                emit([""])
                emit([f"; --- stroke {st.id} ({st.kind}) len={_f(seg_len, 2)}mm ---"])

                # ① 确保阀关（幂等，防上一条异常遗留）
                # 用状态去重：上一条笔画结束时已经关过就不重复发。
                # 网口板每条指令 200ms 开销，这一项能省 10~15% 指令数。
                if emit_ensure_closed and st.start.level != 0:
                    emit_valve(st.start.io, 0)

                # ②③ 空驶到起点 + 降到位
                #
                # ⚠️ 不能把「抬针」和「空驶」合并成一条斜线：
                # 那样针头会贴着工件表面斜着蹭过去，撞坏已打印的格栅。
                # 正确做法是 **先竖直抬到安全高度，再水平空驶，最后竖直落下**，
                # 也就是必须保持两段（甚至三段）独立动作。
                #
                # 唯一能省的是：如果上一条笔画末尾已经抬到 z+lz，
                # 而 z+lz 已经高于本层的安全高度，就不用再抬一次。
                emit(dialect.travel(ctx, pts[0][0], pts[0][1], m.feed_rapid))
                emit(dialect.move_z(ctx, z, lf))

                # ④ 开阀
                emit_valve(st.start.io, st.start.level)

                # ⑤ 上升延时
                emit_dwell(rise)

                # ⑥ 打印段
                # Δ = v(mm/s) × t(s) = (feed/60) × (tail/1000)
                back = (feed / 60.0) * (tail / 1000.0)
                end = pts[-1]

                if dialect.queued_io:
                    # ── IO 随运动排队（Klipper）：不需要切一刀 ──
                    for i in range(1, len(pts)):
                        emit(dialect.print_move(ctx, pts[i][0], pts[i][1], feed))
                    emit_valve(st.end.io, st.end.level)
                    emit_dwell(tail)
                elif back > 1e-6:
                    # ── IO 不排队（Marlin/自研板）：终点前切一刀停车关阀 ──
                    cut, cut_idx = _backtrack(pts, back)
                    if cut_idx == 0:
                        warnings.append(
                            f"stroke {st.id}: 路径总长 {_f(seg_len, 2)}mm < 回退距离 "
                            f"{_f(back, 3)}mm，切点钳到起点"
                        )
                    for i in range(1, cut_idx):
                        emit(dialect.print_move(ctx, pts[i][0], pts[i][1], feed))
                    emit(dialect.print_move(ctx, cut[0], cut[1], feed))
                    # ⑦ 关阀（此时停在 cut 点）
                    emit_valve(st.end.io, st.end.level)
                    # ⑧ 拖尾驻留
                    emit_dwell(tail)
                    # ⑨ 走完剩余 Δ
                    emit(dialect.print_move(ctx, end[0], end[1], feed))
                else:
                    for i in range(1, len(pts)):
                        emit(dialect.print_move(ctx, pts[i][0], pts[i][1], feed))
                    emit_valve(st.end.io, st.end.level)
                    emit_dwell(tail)

                # ⑩ 抬针断料
                # 抬针有两个作用，都不能省：
                #   1) 断料：抬起时针头离开工件，余料自然断开
                #   2) 让下一次空驶有安全间隙，不会蹭到已打印的格栅
                # 所以「抬针 → 空驶 → 落针」必须是三段独立动作，
                # 合并成斜线会让针头贴着工件表面蹭过去。
                emit(dialect.move_z(ctx, z + lz, lf))

    emit([""])
    emit(dialect.footer(ctx))

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
        delays=delays,
        dwell_mode=dwell_mode,
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
