"""离线仿真器：G-code → 位置/IO 时间线。

这是渲染器的数据来源，也是「没有硬件时唯一的真机」。

运动模型：梯形速度曲线（加速-匀速-减速），加速度取 M204/Machine.accel。
不模拟 look-ahead（保守）——这也符合自研板当前可能的状态。

输出：
  samples: [(t, x, y, z, v, feed_active)]
  io_events: [(t, channel, level)]
  segments: [(t0, t1, x0,y0, x1,y1, is_print)]
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field


@dataclass
class Sample:
    t: float  # 秒
    x: float
    y: float
    z: float
    v: float  # mm/s


@dataclass
class IOEventT:
    t: float
    channel: int
    level: int


@dataclass
class Segment:
    t0: float
    t1: float
    x0: float
    y0: float
    x1: float
    y1: float
    z: float
    is_print: bool
    v_peak: float


@dataclass
class SimResult:
    duration: float = 0.0
    samples: list[Sample] = field(default_factory=list)
    io_events: list[IOEventT] = field(default_factory=list)
    segments: list[Segment] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    max_v: float = 0.0

    def to_dict(self, max_samples: int = 4000) -> dict:
        n = len(self.samples)
        step = max(1, n // max_samples)
        s = self.samples[::step]
        return {
            "duration": round(self.duration, 3),
            "max_v": round(self.max_v, 2),
            "samples": [[round(q.t, 3), round(q.x, 3), round(q.y, 3), round(q.z, 3), round(q.v, 2)] for q in s],
            "io_events": [[round(e.t, 3), e.channel, e.level] for e in self.io_events],
            "segments": [
                [
                    round(g.t0, 3),
                    round(g.t1, 3),
                    round(g.x0, 3),
                    round(g.y0, 3),
                    round(g.x1, 3),
                    round(g.y1, 3),
                    round(g.z, 3),
                    1 if g.is_print else 0,
                    round(g.v_peak, 2),
                ]
                for g in self.segments
            ],
            "warnings": self.warnings,
        }


def _trapezoid(d: float, vmax: float, accel: float) -> tuple[float, float]:
    """返回 (总时长, 实际峰值速度)。d 单位 mm，vmax mm/s，accel mm/s²。"""
    if d <= 1e-9:
        return 0.0, 0.0
    if vmax <= 0:
        vmax = 1.0
    # 加速+减速距离
    d_acc = vmax * vmax / accel
    if d >= d_acc:
        t = d / vmax + vmax / accel
        return t, vmax
    # 三角形
    vpk = math.sqrt(d * accel)
    return 2.0 * vpk / accel, vpk


def simulate(gcode: str, accel: float = 300.0, dt: float = 0.01) -> SimResult:
    """解析 G-code 并推进虚拟时钟。"""
    res = SimResult()
    x = y = z = 0.0
    feed = 1500.0  # mm/min 默认
    t = 0.0
    pending_wait = 0.0  # M400 不产生额外时间（因为我们本来就逐段推进）
    abs_mode = True

    lines = gcode.splitlines()
    # 采样缓存：(t, x, y, z, v)
    samples: list[Sample] = []
    # Klipper 的 SET_PIN 用名字，按首次出现顺序映射成稳定通道号
    pin_registry: dict[str, int] = {}

    def add_ramp(t0: float, x0, y0, z0, x1, y1, z1, dur: float, vpk: float):
        """在 [t0, t0+dur] 内按梯形插值采样。"""
        if dur <= 0:
            return
        d = math.dist((x0, y0, z0), (x1, y1, z1))
        if d <= 1e-9:
            return
        n = max(2, int(math.ceil(dur / dt)))
        acc_t = vpk / accel if vpk > 0 else 0.0
        for i in range(n + 1):
            f = i / n
            tt = t0 + dur * f
            # 梯形速度积分 → 位移比例
            if acc_t > 0 and dur >= 2 * acc_t:
                if tt - t0 < acc_t:
                    tau = tt - t0
                    s = 0.5 * accel * tau * tau
                    v = accel * tau
                elif tt - t0 > dur - acc_t:
                    tau = dur - (tt - t0)
                    s = d - 0.5 * accel * tau * tau
                    v = accel * tau
                else:
                    s = 0.5 * accel * acc_t * acc_t + vpk * ((tt - t0) - acc_t)
                    v = vpk
            else:
                # 三角形
                half = dur / 2
                if tt - t0 < half:
                    tau = tt - t0
                    s = 0.5 * accel * tau * tau
                    v = accel * tau
                else:
                    tau = dur - (tt - t0)
                    s = d - 0.5 * accel * tau * tau
                    v = accel * tau
            s = max(0.0, min(d, s))
            r = s / d
            samples.append(
                Sample(
                    t=tt,
                    x=x0 + (x1 - x0) * r,
                    y=y0 + (y1 - y0) * r,
                    z=z0 + (z1 - z0) * r,
                    v=v,
                )
            )

    for raw in lines:
        s = raw.split(";")[0].strip()
        if not s:
            continue
        up = s.upper()

        # ── 模态 ──
        if up.startswith("G90"):
            abs_mode = True
            continue
        if up.startswith("G91"):
            abs_mode = False
            continue
        if up.startswith("M204"):
            # A<accel>
            for tok in up.split():
                if tok.startswith("A"):
                    try:
                        accel = float(tok[1:])
                    except ValueError:
                        pass
            continue
        if up.startswith("M203"):
            for tok in up.split():
                if tok.startswith("V"):
                    try:
                        # 快速速度，暂不单独建模
                        pass
                    except ValueError:
                        pass
            continue

        # ── IO ──
        if up.startswith("M121"):
            ch = 0
            lv = 0
            for tok in up.split():
                if tok.startswith("U"):
                    ch = int(float(tok[1:]))
                elif tok.startswith("V"):
                    lv = int(float(tok[1:]))
            res.io_events.append(IOEventT(t=t, channel=ch, level=lv))
            continue
        if up.startswith("M42"):
            pin = 0
            lv = 0
            for tok in up.split():
                if tok.startswith("P"):
                    pin = int(float(tok[1:]))
                elif tok.startswith("S"):
                    lv = int(float(tok[1:]))
            res.io_events.append(IOEventT(t=t, channel=pin, level=lv))
            continue
        if up.startswith("SET_PIN"):
            pinname = "?"
            lv = 0
            for tok in up.split():
                if tok.startswith("PIN="):
                    pinname = tok[4:]
                elif tok.startswith("VALUE="):
                    lv = int(float(tok[6:]))
            # 通道号按「首次出现顺序」稳定分配，不能用 hash()（跨进程不稳定）
            if pinname not in pin_registry:
                pin_registry[pinname] = len(pin_registry)
            res.io_events.append(IOEventT(t=t, channel=pin_registry[pinname], level=lv))
            continue

        # ── 驻留 ──
        if up.startswith("G4"):
            ms = 0.0
            for tok in up.split():
                if tok.startswith("P"):
                    ms = float(tok[1:])
                elif tok.startswith("S"):
                    ms = float(tok[1:]) * 1000.0
            t += ms / 1000.0
            continue

        # ── 运动 ──
        if up.startswith("G0") or up.startswith("G1"):
            is_print = up.startswith("G1")
            nx, ny, nz = x, y, z
            for tok in up.split():
                try:
                    if tok.startswith("X"):
                        nx = float(tok[1:]) if abs_mode else x + float(tok[1:])
                    elif tok.startswith("Y"):
                        ny = float(tok[1:]) if abs_mode else y + float(tok[1:])
                    elif tok.startswith("Z"):
                        nz = float(tok[1:]) if abs_mode else z + float(tok[1:])
                    elif tok.startswith("F"):
                        feed = float(tok[1:])
                except ValueError:
                    pass
            d = math.dist((x, y, z), (nx, ny, nz))
            if d > 1e-9 and feed > 0:
                vmax = feed / 60.0
                dur, vpk = _trapezoid(d, vmax, accel)
                add_ramp(t, x, y, z, nx, ny, nz, dur, vpk)
                res.segments.append(
                    Segment(
                        t0=t, t1=t + dur, x0=x, y0=y, x1=nx, y1=ny,
                        z=nz, is_print=is_print, v_peak=vpk,
                    )
                )
                t += dur
                res.max_v = max(res.max_v, vpk)
            x, y, z = nx, ny, nz
            continue

        # G28 / G21 / G92 等：忽略
        continue

    # 按时间排序采样
    samples.sort(key=lambda q: q.t)
    res.samples = samples
    res.duration = t
    if not samples:
        res.warnings.append("没有运动采样点")
    return res


def find_io_transitions(res: SimResult) -> list[dict]:
    """给渲染器用：每次 IO 变化时，机器在哪、在不在动。

    用二分查找而非线性扫描——采样点可能上万，IO 事件也可能上百，
    线性扫描是 O(n×m)，规模大时会卡几十秒。
    """
    import bisect

    out = []
    samples = res.samples
    if not samples:
        for ev in res.io_events:
            out.append({
                "t": round(ev.t, 3), "channel": ev.channel, "level": ev.level,
                "x": None, "y": None, "v": None, "moving": None,
            })
        return out

    # 预建时间索引，所有事件共用
    times = [s.t for s in samples]
    n = len(samples)

    for ev in res.io_events:
        i = bisect.bisect_left(times, ev.t)
        if i <= 0:
            near = samples[0]
        elif i >= n:
            near = samples[-1]
        else:
            # 比较左右两个谁更近
            a, b = samples[i - 1], samples[i]
            near = a if (ev.t - a.t) <= (b.t - ev.t) else b
        out.append({
            "t": round(ev.t, 3),
            "channel": ev.channel,
            "level": ev.level,
            "x": round(near.x, 3),
            "y": round(near.y, 3),
            "v": round(near.v, 2),
            "moving": near.v > 0.5,
        })
    return out
