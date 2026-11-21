"""网口板完整端到端：格栅块 → G-code → 真板子（IO + 延时 + 运动）。

全程不碰 G4（会卡死板子），延时靠上位机 sleep。
"""

from __future__ import annotations

import json
import socket
import time
import urllib.request

BASE = "http://127.0.0.1:8765"
IP, PORT = "192.168.0.102", 5000


def get(p):
    return json.load(urllib.request.urlopen(BASE + p))


def post(p, body, timeout=300):
    req = urllib.request.Request(
        BASE + p, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    return json.load(urllib.request.urlopen(req, timeout=timeout))


def udp(cmd, timeout=10.0):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(timeout)
    try:
        s.sendto(cmd.encode() + b"\r", (IP, PORT))
        t0 = time.time()
        raw = ""
        while time.time() - t0 < timeout:
            try:
                d, _ = s.recvfrom(4096)
            except socket.timeout:
                return None, raw
            raw += d.decode("ascii", errors="replace")
            for part in raw.replace("\r", "\n").split("\n"):
                p = part.strip()
                if p.startswith("E") and len(p) >= 4 and p[1:4].isdigit():
                    return p, raw
        return None, raw
    finally:
        s.close()


def io_out():
    code, raw = udp("M120", timeout=5)
    if code != "E000":
        return None
    import re
    m = re.search(r"IOOUT (0x[0-9A-Fa-f]{2})", raw)
    return int(m.group(1), 16) if m else None


def pos():
    import re
    code, raw = udp("M114", timeout=5)
    if code != "E000":
        return None
    m = re.search(r"X:(-?[\d.]+)\s+Y:(-?[\d.]+)\s+Z:(-?[\d.]+)", raw)
    return (float(m.group(1)), float(m.group(2)), float(m.group(3))) if m else None


def main():
    print("=" * 70)
    print("网口板端到端：格栅块 → G-code → 真板子")
    print("=" * 70)

    # 0. 检查板子
    print("\n[0] 板子状态")
    c, _ = udp("M114")
    if c != "E000":
        print(f"    ✗ 板子异常: {c}，请断电重启")
        return 1
    print(f"    ✓ 空闲，位置 {pos()}，IOOUT 0x{io_out():02X}")

    # 1. 归零
    print("\n[1] 归零")
    for cmd in ["G90", "G0 X0 Y0 Z0 F3000"]:
        c, _ = udp(cmd, timeout=20)
        print(f"    {cmd:20s} -> {c}")
    print(f"    归零后: {pos()}")

    # 2. 构造 8mm 格栅块工程
    print("\n[2] 构造格栅块工程（8mm 见方，格栅 2mm，1 层）")
    proj = {
        "version": 1, "name": "nic-lattice",
        "machine": {"name": "lc", "travel_x": 200, "travel_y": 200, "travel_z": 50,
                    "soft_limit": True, "has_home_switch": False,
                    "feed_default": 600, "feed_rapid": 3000, "accel": 300},
        "process": {"rise_ms": 150, "tail_ms": 100, "lift_z": 0.3, "lift_feed": 3000,
                    "line_width": 0.45, "layer_height": 0.4, "min_segment_len": 0.5},
        "layers": [{
            "id": "L1", "z": 0, "repeat": 1, "z_step": 0.4, "overrides": {}, "visible": True,
            "strokes": [{
                "id": "outline",
                "points": [{"x": 0, "y": 0}, {"x": 8, "y": 0}, {"x": 8, "y": 8},
                           {"x": 0, "y": 8}, {"x": 0, "y": 0}],
                "closed": True, "kind": "contour",
                "start": {"io": 0, "level": 1, "delay_ms": 150},
                "end": {"io": 0, "level": 0, "delay_ms": 100},
                "source": {"type": "rect", "x": 0, "y": 0, "w": 8, "h": 8},
            }],
        }],
        "calibration_profile_id": None,
    }
    # 用 API 填充格栅
    fr = post("/api/fill", {
        "source": {"type": "rect", "x": 0, "y": 0, "w": 8, "h": 8},
        "pattern": "grid", "spacing": 2.0, "inset": 0.4, "min_len": 0.5,
        "sort": True, "merge": False, "stagger": False, "outline": False,
    })
    print(f"    格栅填充: {fr['count']} 条筋")
    for i, p in enumerate(fr["paths"]):
        proj["layers"][0]["strokes"].append({
            "id": f"fill{i}",
            "points": [{"x": x, "y": y} for x, y in p],
            "closed": False, "kind": "fill",
            "start": {"io": 0, "level": 1, "delay_ms": 150},
            "end": {"io": 0, "level": 0, "delay_ms": 100},
            "source": None,
        })
    print(f"    共 {len(proj['layers'][0]['strokes'])} 笔画")

    # 3. 编译
    print("\n[3] 编译（dwell=host，无 G4）")
    c = post("/api/compile", {"project": proj, "dialect": "lc"})
    st = c["stats"]
    print(f"    {st['strokes']} 笔画, {st['lines']} 行, {st['path_length']}mm")
    print(f"    dwell 模式: {c['dialect']['dwell']}")
    print(f"    上位机延时总计: {st['host_dwell_seconds']}s")
    nz = [(i, d) for i, d in enumerate(c["delays"]) if d > 0]
    print(f"    非零延时: {len(nz)} 处，前 4 个: {nz[:4]}")
    assert "G4" not in c["gcode"], "G-code 里不该有 G4！"
    print("    ✓ G-code 中无 G4")

    # 4. 下发
    print("\n[4] 下发到板子")
    r = post("/api/link/connect", {"ip": IP, "port": PORT})
    print(f"    {r['message']}")

    t0 = time.time()
    r = post("/api/job/start", {
        "gcode": c["gcode"], "ip": IP, "port": PORT, "delays": c["delays"],
    })
    print(f"    {r['message']}")
    if not r["ok"]:
        return 1

    last = ""
    io_seen = []
    while True:
        time.sleep(0.4)
        s = get(f"/api/job/status?ip={IP}&port={PORT}")
        line = f"{s['done']}/{s['total']} · {s['errors']}错 · {s['elapsed']}s"
        if line != last:
            print(f"    {line}  当前: {s['current'][:36]}")
            last = line
            # 采样 IO 状态（只在 M121 行附近）
            cur = s["current"]
            if cur.startswith("M121"):
                v = io_out()
                io_seen.append((s["done"], cur, v))
        if not s["running"]:
            print(f"\n    最终: {s['message']}")
            if s["log"]:
                print("    错误:")
                for lg in s["log"][:5]:
                    print(f"      {lg}")
            break
        if time.time() - t0 > 300:
            print("    超时")
            post("/api/job/stop", {"ip": IP, "port": PORT})
            break

    # 5. 验证
    print("\n[5] 验证最终状态")
    p = pos()
    io = io_out()
    print(f"    位置: {p}")
    print(f"    IOOUT: 0x{io:02X}")
    if io == 0:
        print("    ✓ 气阀已关闭（安全状态）")
    else:
        print(f"    ⚠ 气阀未关！IOOUT=0x{io:02X}")

    # 6. IO 变化采样
    print("\n[6] 下发过程中采到的 IO 变化")
    for rec in io_seen[:10]:
        print(f"    第{rec[0]:>3}行 {rec[1]:<14} -> IOOUT 0x{rec[2]:02X}")
    if len(io_seen) > 10:
        print(f"    …共 {len(io_seen)} 次采样")

    print("\n" + "=" * 70)
    print("完成")
    print("=" * 70)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
