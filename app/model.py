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
    start: IOEvent = Field(default_factory=lambda: IOEvent(level=1))
    end: IOEvent = Field(default_factory=lambda: IOEvent(level=0))
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
    """全局工艺默认值。层和笔画可以覆盖。"""

    rise_ms: int = Field(120, ge=0, description="开阀后驻留")
    tail_ms: int = Field(80, ge=0, description="关阀后驻留")
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


DIALECTS: dict[str, DialectCaps] = {
    "lc": DialectCaps(queued_io=True, arcs=False, max_line_len=128, dwell_unit="ms",
                      dwell="host"),
    "marlin": DialectCaps(queued_io=False, arcs=True, max_line_len=96, dwell_unit="ms",
                          dwell="gcode"),
    "klipper": DialectCaps(queued_io=True, arcs=True, max_line_len=0, dwell_unit="ms",
                           dwell="gcode"),
    "f1": DialectCaps(queued_io=True, arcs=False, max_line_len=89, dwell_unit="ms",
                      dwell="none"),
}
