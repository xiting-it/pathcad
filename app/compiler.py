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

from .dialects import Ctx, Dialect, _f, render_instr
from .ir import (
    KIND_COMMENT,
    KIND_DWELL,
    KIND_MOVE,
    Instr,
    Program,
    comment,
    dwell,
    modal,
    move as ir_move,
    tool_off,
    tool_on,
)
from .model import (
    IOEvent,
    Layer,
    Machine,
    Process,
    Project,
    Stroke,
    migrate_project,
    tool_index,
)


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
    # 结构化指令序列（P3 引入）。方言文本是它的渲染结果。
    program: Program | None = None

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
    """把整个工程编译成指令序列（IR），再渲染成目标方言的文本。

    P3 起的工作方式：
      ① 笔画 → IR（与控制器无关：移动 / 开工具 / 驻留 / 关工具）
      ② IR → 方言文本（每种板子自己决定怎么说）

    延时有两种落法（见 model.DialectCaps.dwell）：
      - "gcode"：渲染成 G4 行，延时就在文本里
      - "host" ：不渲染，延时要记在 delays[] 里，由下发器 sleep
      - "none" ：直接丢弃
    """
    # 保证有工具、笔画都指向有效工具（旧工程在这里被迁移）
    migrate_project(project)
    tools = tool_index(project)
    default_tool = project.tools[0]

    m = project.machine
    p = project.process

    # 轴方向反转：把「工件坐标」映射成「机器坐标」。
    # 电机接线装反时，命令 +10 实际往负方向跑，取负即可纠正。
    invx = -1.0 if getattr(m, "invert_x", False) else 1.0
    invy = -1.0 if getattr(m, "invert_y", False) else 1.0
    invz = -1.0 if getattr(m, "invert_z", False) else 1.0

    def MX(x: float) -> float:
        return x * invx

    def MY(y: float) -> float:
        return y * invy

    def MZ(z: float) -> float:
        return z * invz
    prog = Program()
    warnings: list[str] = []
    stroke_n = 0
    total_len = 0.0
    used_tools: list[str] = []

    dwell_mode = getattr(getattr(dialect, "caps", None), "dwell", None) or "gcode"
    ctx = Ctx(machine=m, process=p, z=0.0)

    # 输出通道状态：只在电平真的变化时才发指令。
    # 每条笔画开头一次「确保关闭」大部分是冗余的，去掉能省 10~15% 指令数。
    io_level: dict[int, int] = {}

    def emit(ins: Instr) -> None:
        prog.append(ins)

    def emit_text(text: str = "") -> None:
        emit(comment(text))

    def emit_tool_on(tool) -> None:
        io_level[tool.on_channel] = tool.on_level
        if tool.id not in used_tools:
            used_tools.append(tool.id)
        emit(tool_on(tool_id=tool.id, channel=tool.on_channel, level=tool.on_level))

    def emit_tool_off(tool, force: bool = False) -> None:
        ch = tool.close_channel()
        if not force and io_level.get(ch) == tool.off_level:
            return
        io_level[ch] = tool.off_level
        emit(tool_off(tool_id=tool.id, channel=ch, level=tool.off_level))

    def emit_dwell(ms: int) -> None:
        if ms > 0:
            emit(dwell(ms))

    # 头部：方言自己决定要哪些模态指令
    for line in dialect.header(ctx):
        emit(modal(line))

    lift = p.lift_z

    # ── 安全高度 ──
    # 作用只是「XY 空驶时把针抬起来别蹭到工件」，不需要抬到机器顶部。
    #
    # ⚠️ 曾经写成 max(m.travel_z, lift+1)，结果默认 travel_z=100 就生成
    #    `G0 Z100`。如果机器的 Z 实际行程只有几毫米，这一条会直接顶着
    #    限位堵转 —— 电机满电流不转，几分钟就烧。这是真事过的事故。
    #
    # 正确做法：抬到「本工程最高一层 + 抬针高度 + 余量」，再受 travel_z 约束。
    top_z = 0.0
    for _ly in project.layers:
        if _ly.visible and _ly.strokes:
            top_z = max(top_z, _ly.z + max(0, _ly.repeat - 1) * _ly.z_step)
    if safe_z is not None:
        safe = safe_z
    else:
        safe = top_z + lift + 1.0
    # 无论如何不能超过机器行程
    if m.travel_z > 0:
        safe = min(safe, m.travel_z)
    warnings.append(
        f"安全高度 {safe:g}mm（最高层 {top_z:g} + 抬针 {lift:g} + 1），"
        f"机器行程 {m.travel_z:g}mm"
    ) if safe >= m.travel_z > 0 else None

    emit_text(f"; lift to safe z={_f(safe)}")
    emit(ir_move(z=MZ(safe), feed=p.lift_feed, rapid=True))

    for layer in project.layers:
        if not layer.visible or not layer.strokes:
            continue
        for rep in range(layer.repeat):
            z = layer.z + rep * layer.z_step
            emit_text("")
            emit_text(f"; === layer {layer.id} rep {rep + 1}/{layer.repeat} z={_f(z)} ===")

            for st in layer.strokes:
                tool = tools.get(st.tool) or default_tool
                if not tool.enabled:
                    continue

                eff = _effective(p, layer, st)
                feed = st.feed or m.feed_default
                rise = int(tool.rise_ms)
                tail = int(tool.tail_ms)
                lz = float(eff["lift_z"])
                lf = float(eff["lift_feed"])

                pts = [(q.x, q.y) for q in st.points]
                if len(pts) < 2:
                    warnings.append(f"stroke {st.id}: 点数不足 2，跳过")
                    continue

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

                emit_text("")
                emit_text(f"; --- stroke {st.id} ({st.kind}) tool={tool.id} "
                          f"len={_f(seg_len, 2)}mm ---")

                # ① 确保工具关（幂等，防上一条异常遗留）
                if emit_ensure_closed and tool.on_level != 0:
                    emit_tool_off(tool)

                # ②③ 空驶到起点 + 降到位
                # ⚠️ 不能把「抬针」和「空驶」合并成斜线：
                # 那样针头会贴着工件表面斜着蹭过去，撞坏已打印的格栅。
                emit(ir_move(x=MX(pts[0][0]), y=MY(pts[0][1]), feed=m.feed_rapid, rapid=True))
                emit(ir_move(z=MZ(z), feed=lf, rapid=True))

                # ④ 开工具  ⑤ 上升延时
                emit_tool_on(tool)
                emit_dwell(rise)

                # ⑥ 打印段
                back = (feed / 60.0) * (tail / 1000.0)
                end_pt = pts[-1]

                if dialect.queued_io:
                    # IO 随运动排队：不需要切一刀
                    for i in range(1, len(pts)):
                        emit(ir_move(x=MX(pts[i][0]), y=MY(pts[i][1]), feed=feed))
                    emit_tool_off(tool, force=True)
                    emit_dwell(tail)
                elif back > 1e-6:
                    # IO 不排队：终点前切一刀停车关阀
                    cut, cut_idx = _backtrack(pts, back)
                    if cut_idx == 0:
                        warnings.append(
                            f"stroke {st.id}: 路径总长 {_f(seg_len, 2)}mm < 回退距离 "
                            f"{_f(back, 3)}mm，切点钳到起点"
                        )
                    for i in range(1, cut_idx):
                        emit(ir_move(x=MX(pts[i][0]), y=MY(pts[i][1]), feed=feed))
                    emit(ir_move(x=MX(cut[0]), y=MY(cut[1]), feed=feed))
                    emit_tool_off(tool, force=True)
                    emit_dwell(tail)
                    emit(ir_move(x=MX(end_pt[0]), y=MY(end_pt[1]), feed=feed))
                else:
                    for i in range(1, len(pts)):
                        emit(ir_move(x=MX(pts[i][0]), y=MY(pts[i][1]), feed=feed))
                    emit_tool_off(tool, force=True)
                    emit_dwell(tail)

                # ⑩ 抬针断料（不可省，理由见上）
                emit(ir_move(z=MZ(z + lz), feed=lf, rapid=True))

    emit(comment(""))
    for line in dialect.footer(ctx):
        emit(modal(line))

    # ── IR → 文本 ──
    out: list[str] = []
    delays: list[float] = []
    pending_ms = 0

    for ins in prog.instrs:
        if ins.kind == KIND_DWELL:
            if dwell_mode == "gcode":
                rendered = render_instr(dialect, ins, ctx)
                out.extend(rendered)
                delays.extend([0.0] * len(rendered))
            elif dwell_mode == "host":
                pending_ms += ins.ms
            # none：丢弃
            continue

        rendered = render_instr(dialect, ins, ctx)
        if not rendered:
            continue
        out.extend(rendered)
        d = [0.0] * len(rendered)
        if pending_ms:
            d[-1] += pending_ms / 1000.0
            pending_ms = 0
        delays.extend(d)

    # 行长度校验
    if getattr(dialect, "max_line_len", 0):
        for ln in out:
            if len(ln) > dialect.max_line_len:
                warnings.append(
                    f"行超长 {len(ln)}B > {dialect.max_line_len}B: {ln[:40]}..."
                )

    prog.stroke_count = stroke_n
    prog.path_length = total_len
    prog.tool_used = used_tools

    return Compiled(
        text=chr(10).join(out) + chr(10),
        lines=out,
        stroke_count=stroke_n,
        path_length=total_len,
        warnings=warnings,
        delays=delays,
        dwell_mode=dwell_mode,
        program=prog,
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
