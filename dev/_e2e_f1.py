"""端到端验证：pathCAM 图形 → G-code → 真板子运动。

用一个小方格（8mm 见方，格栅填充，1 层），下发到 COM7 真板子，
观察坐标是否按预期变化。
"""

from __future__ import annotations

import json
import time
import urllib.request

BASE = "http://127.0.0.1:8765"
PORT = "COM7"


def get(p):
    return json.load(urllib.request.urlopen(BASE + p))


def post(p, body):
    req = urllib.request.Request(
        BASE + p, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    return json.load(urllib.request.urlopen(req, timeout=300))


def main():
    print("=" * 64)
    print("端到端：图形 → G-code → 真板子")
    print("=" * 64)

    # 1. 连接
    print("\n[1] 连接板子")
    r = post("/api/serial/connect", {"port": PORT, "baud": 115200})
    print(f"    {r['message']}")
    if not r["ok"]:
        return 1

    # 2. 回原点（用 G00，不用 G28 以免跑飞）
    print("\n[2] 归位到 (0,0,0)")
    for cmd in ["G00 X0 Y0 Z0"]:
        r = post("/api/serial/send-one", {"port": PORT, "line": cmd})
        print(f"    {cmd} -> {r['raw']}")

    # 3. 构造一个 8mm 见方的方块，格栅填充
    print("\n[3] 构造工程：8mm 方块 + 格栅填充")
    proj = {
        "version": 1, "name": "e2e-test",
        "machine": {"name": "f1", "travel_x": 200, "travel_y": 200, "travel_z": 50,
                    "soft_limit": True, "has_home_switch": False,
                    "feed_default": 600, "feed_rapid": 3000, "accel": 300},
        "process": {"rise_ms": 0, "tail_ms": 0, "lift_z": 0.2, "lift_feed": 3000,
                    "line_width": 0.4, "layer_height": 0.4, "min_segment_len": 0.5},
        "layers": [], "calibration_profile_id": None,
    }
    # 直接构造笔画：外框 + 十字格栅
    strokes = []
    sq = [(0, 0), (8, 0), (8, 8), (0, 8), (0, 0)]
    strokes.append({"id": "outline", "points": [{"x": x, "y": y} for x, y in sq],
                    "closed": True, "kind": "contour",
                    "start": {"io": 0, "level": 1, "delay_ms": 0},
                    "end": {"io": 0, "level": 0, "delay_ms": 0},
                    "source": {"type": "rect", "x": 0, "y": 0, "w": 8, "h": 8}})
    # 内部格栅 4mm 间距
    for y in [2.67, 5.33]:
        strokes.append({"id": f"h{y}", "points": [{"x": 0, "y": y}, {"x": 8, "y": y}],
                        "closed": False, "kind": "fill",
                        "start": {"io": 0, "level": 1, "delay_ms": 0},
                        "end": {"io": 0, "level": 0, "delay_ms": 0}, "source": None})
    for x in [2.67, 5.33]:
        strokes.append({"id": f"v{x}", "points": [{"x": x, "y": 0}, {"x": x, "y": 8}],
                        "closed": False, "kind": "fill",
                        "start": {"io": 0, "level": 1, "delay_ms": 0},
                        "end": {"io": 0, "level": 0, "delay_ms": 0}, "source": None})
    proj["layers"].append({"id": "L1", "z": 0, "repeat": 1, "z_step": 0.4,
                           "overrides": {}, "visible": True, "strokes": strokes})
    print(f"    {len(strokes)} 条笔画")

    # 4. 编译成 G-code（注意：F1 板不支持 M121，所以选 lc 方言会产生 M121；
    #    这里先看生成的 G-code 长什么样）
    print("\n[4] 编译 G-code")
    c = post("/api/compile", {"project": proj, "dialect": "lc"})
    lines = [l for l in c["gcode"].splitlines() if l.strip() and not l.strip().startswith(";")]
    print(f"    {c['stats']['strokes']} 笔画, {len(lines)} 行有效指令")
    print("    前 10 行:")
    for l in lines[:10]:
        print(f"      {l}")

    # 5. 过滤出 F1 板支持的指令（G00/G01/G02/G28/M50）
    print("\n[5] 过滤成 F1 支持的指令集")
    supported = []
    dropped = []
    for l in lines:
        op = l.split()[0].upper() if l.split() else ""
        if op in ("G00", "G0", "G01", "G1", "G02", "G2", "G28", "M50"):
            # 统一成两位写法
            if op in ("G0",):
                l = l.replace("G0 ", "G00 ", 1)
            if op in ("G1",):
                l = l.replace("G1 ", "G01 ", 1)
            if op in ("G2",):
                l = l.replace("G2 ", "G02 ", 1)
            supported.append(l)
        else:
            dropped.append(l)
    print(f"    保留 {len(supported)} 行, 丢弃 {len(dropped)} 行")
    if dropped:
        ops = {}
        for d in dropped:
            op = d.split()[0] if d.split() else "?"
            ops[op] = ops.get(op, 0) + 1
        print(f"    丢弃的指令类型: {ops}")

    # 6. 下发
    print(f"\n[6] 下发到 {PORT}")
    gcode = "\n".join(supported)
    r = post("/api/serial/job/start", {"gcode": gcode, "port": PORT, "baud": 115200})
    print(f"    {r['message']}")
    if not r["ok"]:
        return 1

    t0 = time.time()
    last = ""
    while True:
        time.sleep(1)
        s = get(f"/api/serial/job/status?port={PORT}")
        line = f"{s['done']}/{s['total']} 行, {s['errors']} 错, {s['elapsed']}s"
        if line != last:
            print(f"    {line}  当前: {s['current'][:40]}")
            last = line
        if not s["running"]:
            print(f"\n    最终: {s['message']}")
            if s["log"]:
                print("    错误日志:")
                for lg in s["log"][:5]:
                    print(f"      {lg}")
            break
        if time.time() - t0 > 300:
            print("    超时，中止")
            post("/api/serial/job/stop", {"port": PORT, "baud": 115200})
            break

    # 7. 验证最终位置
    print("\n[7] 验证最终位置")
    r = post("/api/serial/send-one", {"port": PORT, "line": "M50"})
    print(f"    {r['raw']}")

    print("\n" + "=" * 64)
    print("完成")
    print("=" * 64)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
