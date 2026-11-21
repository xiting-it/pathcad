"""量端到端真实耗时：启动、编译、协议下发开销。"""

from __future__ import annotations

import base64
import json
import time
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:8765"


def post(path: str, body: dict) -> dict:
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    return json.load(urllib.request.urlopen(req, timeout=300))


def main() -> None:
    print("=== 端到端真实耗时（齿轮 20 层 / 740 轮廓）===\n")

    raw = Path("samples/02-齿轮.stl").read_bytes()
    b64 = base64.b64encode(raw).decode()

    t0 = time.perf_counter()
    r = post("/api/mesh-slice", {
        "filename": "02-齿轮.stl", "data_b64": b64,
        "layer_height": 0.4, "only_largest": True, "max_layers": 200,
    })
    t_slice = (time.perf_counter() - t0) * 1000
    layers = r.get("layers") or []
    print(f"  切片             {t_slice:8.0f} ms   → {len(layers)} 层")

    # 用切片结果直接拼工程（每层加格栅填充）
    from app.infill import fill_grid  # noqa: PLC0415

    proj_layers = []
    t0 = time.perf_counter()
    for i, ly in enumerate(layers):
        strokes = []
        for p in (ly.get("paths") or []):
            for line in fill_grid(p, spacing=2.0, inset=0.4):
                strokes.append({
                    "id": f"s{len(strokes)}",
                    "points": [{"x": x, "y": y} for x, y in line],
                    "closed": False,
                    "kind": "fill",
                    "start": {"io": 0, "level": 1, "delay_ms": 120},
                    "end": {"io": 0, "level": 0, "delay_ms": 80},
                })
        proj_layers.append({
            "id": f"L{i}", "z": ly.get("z", 0.0), "repeat": 1,
            "z_step": 0.4, "overrides": {}, "visible": True, "strokes": strokes,
        })
    t_fill = (time.perf_counter() - t0) * 1000
    n_stroke = sum(len(L["strokes"]) for L in proj_layers)
    print(f"  全层填充         {t_fill:8.0f} ms   → {n_stroke} 笔画")

    proj = {
        "version": 1, "name": "bench",
        "machine": {"name": "lc-diym", "travel_x": 200, "travel_y": 200,
                    "travel_z": 100, "soft_limit": True, "has_home_switch": False,
                    "feed_default": 600, "feed_rapid": 6000, "accel": 300},
        "process": {"rise_ms": 120, "tail_ms": 80, "lift_z": 0.3, "lift_feed": 3000,
                    "line_width": 0.4, "layer_height": 0.4, "min_segment_len": 1.5},
        "layers": proj_layers,
    }

    t0 = time.perf_counter()
    c = post("/api/compile", {"project": proj, "dialect": "lc"})
    t_compile = (time.perf_counter() - t0) * 1000
    st = c.get("stats") or {}
    print(f"  编译+仿真        {t_compile:8.0f} ms   → {st.get('lines')} 行 G-code")

    total = t_slice + t_fill + t_compile
    print(f"\n  **用户在 UI 里等的时间合计：{total:.0f} ms**")

    # 下发侧：板子每条指令内部开销
    lines = st.get("lines") or 0
    for per in (200, 280, 360):
        sec = lines * per / 1000
        print(f"    真机下发 {lines} 行 × {per}ms/条 = {sec/60:.1f} 分钟（板子固件开销）")


if __name__ == "__main__":
    main()
