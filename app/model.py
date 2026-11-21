"""核心数据模型。

设计要点（见 docs/00-共用内核.md）：
- 笔画是原子的，一条笔画内不做 IO 变化
- 起点/终点事件各自独立（通道、电平、延时）
- 延时是「事件之后」的时间，单位统一 ms
- 工程文件里 Y 向上（和 G-code 一致），只在渲染时翻转
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, field_validator


class IOEvent(BaseModel):
    """一条笔画端点上的 IO 事件。"""

    io: int = Field(0, ge=0, le=7, description="IOOUT 通道号 0-7")
    level: Literal[0, 1] = Field(1, description="1=高电平，0=低电平")
    delay_ms: int = Field(0, ge=0, le=10000, description="事件之后驻留的毫秒数")


class Tool(BaseModel):
    """工具 = 一套输出参数 + 一个物理通道。

    这是 P3 引入的核心抽象：笔画不再各自携带 IO 参数，而是引用一个工具。
    好处是换材料只加/改工具，不用逐条改笔画；以后接激光也只是换 kind 和参数。

    流体（kind="valve"）用 on/off 通道 + 两端延时；
    激光（kind="laser"，预留）会用 power/frequency，届时在 params 里扩展。
    """

    id: str
    name: str = "主挤出"
    color: str = "#4ea3ff"          # 界面显示色，也用于画布着色
    enabled: bool = True            # Output 开关：关掉这个工具，它的笔画不参与输出
    visible: bool = True
    kind: Literal["valve", "laser"] = "valve"

    # 开
    on_channel: int = Field(0, ge=0, le=63)
    on_level: int = Field(1, ge=0, le=1)
    rise_ms: int = Field(120, ge=0, le=10000, description="开阀后驻留")

    # 关（默认和开同通道）
    off_channel: int | None = Field(None, ge=0, le=63, description="None = 同 on_channel")
    off_level: int = Field(0, ge=0, le=1)
    tail_ms: int = Field(80, ge=0, le=10000, description="关阀后驻留")

    def close_channel(self) -> int:
        return self.off_channel if self.off_channel is not None else self.on_channel


class Point(BaseModel):
    x: float
    y: float


class Source(BaseModel):
    """图元的原始描述。存它是为了支持增量编辑——
    用户改矩形宽度时只重算那一个图元，不用重新生成整段点。"""

    type: Literal["polyline", "rect", "circle", "bezier", "svg", "image",
                  "ellipse", "polygon", "diamond", "star"]
    # rect
    x: float | None = None
    y: float | None = None
    w: float | None = None
    h: float | None = None
    # circle / polygon / star
    cx: float | None = None
    cy: float | None = None
    r: float | None = None
    # ellipse / diamond（两个半轴）
    rx: float | None = None
    ry: float | None = None
    # polygon（边数）/ star（角数）
    sides: int | None = None
    points: int | None = None
    # star 内半径
    inner: float | None = None
    # bezier / polyline / svg
    pts: list[Point] | None = None
    # 离散精度（弦高误差 mm）
    tolerance: float | None = None
    # 旋转角度（度，逆时针为正）。绕 rot_cx/rot_cy 旋转，
    # 未指定中心时用图元自身包围盒中心。
    rotation: float | None = None
    rot_cx: float | None = None
    rot_cy: float | None = None


class Stroke(BaseModel):
    """一条笔画 = 一次落笔到抬笔。"""

    id: str
    points: list[Point] = Field(..., min_length=1)
    closed: bool = False
    feed: float | None = Field(None, gt=0, description="mm/min，覆盖全局")
    kind: Literal["contour", "fill", "travel"] = "contour"
    # 新：引用工具 id。None 表示用工程里的第一个工具（迁移期的兜底）
    tool: str | None = None
    # 旧字段：P3 之前每条笔画各自带两端事件。仅为读取旧工程保留，
    # 迁移时会被折算成 tool，之后不再写回。
    start: IOEvent | None = None
    end: IOEvent | None = None
    source: Source | None = None

    @field_validator("points")
    @classmethod
    def _need_two_for_line(cls, v: list[Point]) -> list[Point]:
        return v


class Layer(BaseModel):
    id: str
    z: float = 0.0
    repeat: int = Field(1, ge=1, le=1000)
    z_step: float = Field(0.4, description="repeat 时每层 Z 增量")
    overrides: dict[str, float | int] = Field(default_factory=dict)
    strokes: list[Stroke] = Field(default_factory=list)
    visible: bool = True


class Machine(BaseModel):
    name: str = "lc-diym"
    travel_x: float = 200.0
    travel_y: float = 200.0
    travel_z: float = 100.0
    soft_limit: bool = True
    has_home_switch: bool = False
    feed_default: float = Field(600.0, gt=0, description="mm/min 打印段")
    feed_rapid: float = Field(6000.0, gt=0, description="mm/min 空驶")
    accel: float = Field(300.0, gt=0, description="mm/s²")


class Process(BaseModel):
    """全局工艺配置。

    ⚠️ P3 起 rise_ms / tail_ms 属于**工具**，不再属于全局。
    这里保留这两个字段只为了让旧工程读得进来——迁移时用它们初始化工具，
    之后编译不再读 Process 的这两个值。
    """

    rise_ms: int = Field(120, ge=0, description="（旧）迁移时用作工具初值")
    tail_ms: int = Field(80, ge=0, description="（旧）迁移时用作工具初值")
    lift_z: float = Field(0.3, ge=0, description="抬针高度")
    lift_feed: float = Field(3000.0, gt=0)
    line_width: float = Field(0.4, gt=0, description="线宽 mm，3D 预览用")
    layer_height: float = Field(0.4, gt=0, description="层高 mm，3D 预览用")
    min_segment_len: float = Field(
        1.5, gt=0, description="无 look-ahead 时的最短段长，见 00 文档 §5"
    )


class CalibrationProfile(BaseModel):
    id: str
    material: str = ""
    needle: float = 0.4
    pressure: float = 0.6
    rise_ms: int = 120
    tail_ms: int = 80
    line_width: float = 0.42
    max_feed: float = 900.0
    min_segment_len: float = 1.5
    notes: str = ""


class Project(BaseModel):
    """工程文件。

    app / app_version 是产品来源标识：
      · 新文件写 "cam-b" + 当前版本
      · 旧文件（pathCAM 时代）没有这两个字段，读取时按 None 处理并自动迁移
    """

    version: int = 1
    app: str | None = None            # "cam-b"；None 表示旧工程
    app_version: str | None = None
    name: str = "untitled"
    machine: Machine = Field(default_factory=Machine)
    process: Process = Field(default_factory=Process)
    # 工具列表。为空时由 ensure_tools() 自动补一个默认工具。
    tools: list[Tool] = Field(default_factory=list)
    layers: list[Layer] = Field(default_factory=list)
    calibration_profile_id: str | None = None


class DialectCaps(BaseModel):
    """方言能力声明。

    queued_io 决定后处理器要不要插 M400；
    dwell 决定延时在哪实现：

      "gcode" 板子支持 G4 → 延时写进 G-code，下发器不用管
      "host"  板子不支持 G4 → 编译器不写 G4，改输出 delays[] 元数据，
              下发器在「等 E000 之后」按它 sleep
      "none"  板子既不会 G4 也不该 sleep（纯运动板）→ 延时直接丢弃
    """

    queued_io: bool
    arcs: bool
    max_line_len: int
    dwell_unit: Literal["ms", "s"]
    dwell: Literal["gcode", "host", "none"] = "gcode"
    # IO 输出路数。超过这个数的通道号在下发前就报错，
    # 不要等板子回 010（那样错误信息更难定位）。
    io_channels: int = 8
    # 结果码首字母。R7 全用 E；H5 是 G 行 E、纯 M 行 B。
    # 留成列表是为了让下发器不必区分指令类型就能配对。
    reply_letters: list[str] = ["E"]


DIALECTS: dict[str, DialectCaps] = {
    # R7 网口板：G4 会卡死队列，延时靠上位机 sleep
    "lc": DialectCaps(queued_io=True, arcs=False, max_line_len=128, dwell_unit="ms",
                      dwell="host", io_channels=8, reply_letters=["E"]),
    # H5 网口板：G4 可用且单位是「秒」；IO 实测只有 4 路；
    # 纯 M 行回 B 码、G 行回 E 码
    "h5": DialectCaps(queued_io=True, arcs=False, max_line_len=128, dwell_unit="s",
                      dwell="gcode", io_channels=4, reply_letters=["E", "B"]),
    "marlin": DialectCaps(queued_io=False, arcs=True, max_line_len=96, dwell_unit="ms",
                          dwell="gcode"),
    "klipper": DialectCaps(queued_io=True, arcs=True, max_line_len=0, dwell_unit="ms",
                           dwell="gcode"),
    "f1": DialectCaps(queued_io=True, arcs=False, max_line_len=89, dwell_unit="ms",
                      dwell="none"),
}


# ══════════════════════════════════════════════════════════
# 工具：默认值、旧工程迁移
# ══════════════════════════════════════════════════════════

# 新工具的默认配色（按创建顺序循环取）
TOOL_COLORS = [
    "#4ea3ff",  # 蓝
    "#ffb454",  # 橙
    "#90d19a",  # 绿
    "#c78cff",  # 紫
    "#ff7a7a",  # 红
    "#5ad0d0",  # 青
]


def make_default_tool(
    index: int = 0,
    *,
    channel: int = 0,
    rise_ms: int = 120,
    tail_ms: int = 80,
    name: str | None = None,
) -> Tool:
    """造一个工具。index 决定 id 和默认配色。"""
    return Tool(
        id=f"t{index + 1}",
        name=name or ("主挤出" if index == 0 else f"工具 {index + 1}"),
        color=TOOL_COLORS[index % len(TOOL_COLORS)],
        on_channel=channel,
        rise_ms=rise_ms,
        tail_ms=tail_ms,
    )


def ensure_tools(project: "Project") -> "Project":
    """保证工程至少有一个工具，且每条笔画都指向有效工具。

    三种情况：
      1. 没有工具、笔画也没有旧字段 → 补一个默认工具，全部指向它
      2. 没有工具、笔画带旧 start/end → 按参数组合归类，生成若干工具
      3. 已有工具 → 只修那些 tool 为 None 或指向不存在工具的笔画
    """
    if not project.tools:
        # 收集旧字段的参数组合，决定要生成几个工具
        combos: dict[tuple, list[Stroke]] = {}
        loose: list[Stroke] = []
        for layer in project.layers:
            for st in layer.strokes:
                if st.start is not None and st.end is not None:
                    key = (
                        st.start.io, int(st.start.level), st.start.delay_ms,
                        st.end.io, int(st.end.level), st.end.delay_ms,
                    )
                    combos.setdefault(key, []).append(st)
                else:
                    loose.append(st)

        if combos:
            made: list[Tool] = []
            for i, (key, strokes) in enumerate(combos.items()):
                on_io, on_lv, rise, off_io, off_lv, tail = key
                t = make_default_tool(
                    i,
                    channel=on_io,
                    rise_ms=rise,
                    tail_ms=tail,
                    name="主挤出" if i == 0 else f"工具 {i + 1}",
                )
                t.on_level = on_lv
                t.off_level = off_lv
                if off_io != on_io:
                    t.off_channel = off_io
                made.append(t)
                for st in strokes:
                    st.tool = t.id
            project.tools = made
            default_id = made[0].id
        else:
            t = make_default_tool(0, rise_ms=project.process.rise_ms,
                                  tail_ms=project.process.tail_ms)
            project.tools = [t]
            default_id = t.id

        for st in loose:
            st.tool = default_id
        return project

    # 已有工具：修补悬空引用
    ids = {t.id for t in project.tools}
    fallback = project.tools[0].id
    for layer in project.layers:
        for st in layer.strokes:
            if st.tool not in ids:
                st.tool = fallback
    return project


def tool_index(project: "Project") -> dict[str, Tool]:
    """id → Tool 的索引。"""
    return {t.id: t for t in project.tools}


def migrate_project(p: "Project") -> "Project":
    """把任意来源的工程整理成当前格式。幂等，可重复调用。

    做三件事：
      1. 补 app / app_version 标识
      2. 补齐工具并把笔画指过去
      3. 清掉笔画上的旧 start/end 字段（数据已经折进工具）
    """
    if not p.app:
        p.app = "cam-b"
        p.app_version = None
        p._migrated = True  # type: ignore[attr-defined]

    ensure_tools(p)

    for layer in p.layers:
        for st in layer.strokes:
            if st.start is not None or st.end is not None:
                st.start = None
                st.end = None
    return p
