"""网口板（i.MX RT1170）端到端验证：IO + 运动 + 上位机延时。

关键点：这块板不支持 G4，延时由上位机在「等 E000 之后」sleep。
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


def udp_ask(cmd, timeout=10.0):
    """直接 UDP 发一条，等真正的 E 码（跳过 queued 和 echo）。"""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.settimeout(timeout)
    try:
        s.sendto(cmd.encode() + b"\r", (IP, PORT))
        t0 = time.time()
        while time.time() - t0 < timeout:
            try:
                d, _ = s.recvfrom(4096)
            except socket.timeout:
                return None, None
            t = d.decode("ascii", errors="replace")
            for part in t.replace("\r", "\n").split("\n"):
                p = part.strip()
                if p.startswith("E") and len(p) >= 4 and p[1:4].isdigit():
                    return p, t
        return None, None
    finally:
        s.close()


def main():
    print("=" * 64)
    print("网口板端到端验证")
    print("=" * 64)

    # 0. 先确认板子空闲
    print("\n[0] 检查板子状态")
    code, raw = udp_ask("M114")
    print(f"    M114 -> {code}")
    if code and code.startswith("E02"):
        print("    ⚠ 队列卡死，需要断电重启板子")
        return 1
    if code != "E000":
        print(f"    ⚠ 板子异常：{code}")
        return 1
    print("    板子空闲 ✓")

    # 1. 归零
    print("\n[1] 归零")
    for cmd in ["G90", "G0 X0 Y0 Z0 F3000"]:
        c, _ = udp_ask(cmd, timeout=20)
        print(f"    {cmd:20s} -> {c}")

    # 2. 构造工程：5mm 方块 + 延时
    print("\n[2] 编译（rise=120ms, tail=80ms）")
    proj = {
        "version": 1, "name": "nic-e2e",
        "machine": {"name": "lc", "travel_x": 200, "travel_y": 200, "travel_z": 50,
                    "soft_limit": True, "has_home_switch": False,
                    "feed_default": 600, "feed_rapid": 3000, "accel": 300},
        "process": {"rise_ms": 120, "tail_ms": 80, "lift_z": 0.2, "lift_feed": 3000,
                    "line_width": 0.4, "layer_height": 0.4, "min_segment_len": 0.5},
        "layers": [{
            "id": "L1", "z": 0, "repeat": 1, "z_step": 0.4, "overrides": {}, "visible": True,
            "strokes": [{
                "id": "sq", "points": [
                    {"x": 0, "y": 0}, {"x": 5, "y": 0}, {"x": 5, "y": 5},
                    {"x": 0, "y": 5}, {"x": 0, "y": 0},
                ], "closed": True, "kind": "contour",
                "start": {"io": 0, "level": 1, "delay_ms": 120},
                "end": {"io": 0, "level": 0, "delay_ms": 80},
                "source": {"type": "rect", "x": 0, "y": 0, "w": 5, "h": 5},
            }],
        }],
        "calibration_profile_id": None,
    }
    c = post("/api/compile", {"project": proj, "dialect": "lc"})
    print(f"    {c['stats']['strokes']} 笔画, {c['stats']['lines']} 行, "
          f"上位机延时 {c['stats']['host_dwell_seconds']}s")
    print(f"    dwell 模式: {c['dialect']['dwell']}")

    # 3. 连接并下发
    print("\n[3] 连接并发下")
    r = post("/api/link/connect", {"ip": IP, "port": PORT})
    print(f"    {r['message']}")
    if not r["ok"]:
        return 1

    t0 = time.time()
    r = post("/api/job/start", {
        "gcode": c["gcode"], "ip": IP, "port": PORT, "delays": c["delays"],
    })
    print(f"    {r['message']}")
    if not r["ok"]:
        return 1

    last = ""
    while True:
        time.sleep(0.5)
        s = get(f"/api/job/status?ip={IP}&port={PORT}")
        line = f"{s['done']}/{s['total']} 行 · {s['errors']} 错 · {s['elapsed']}s"
        if line != last:
            print(f"    {line}  当前: {s['current'][:40]}")
            last = line
        if not s["running"]:
            print(f"\n    最终: {s['message']}")
            if s["log"]:
                for lg in s["log"][:5]:
                    print(f"      {lg}")
            break
        if time.time() - t0 > 180:
            print("    超时")
            break

    # 4. 验证最终状态
    print("\n[4] 验证最终状态")
    code, raw = udp_ask("M114")
    print(f"    M114 -> {code}")
    code2, raw2 = udp_ask("M120")
    print(f"    M120 -> {code2}")
    if raw2:
        for part in raw2.replace("\r", "\n").split("\n"):
            if "IOOUT" in part or "IOIN" in part:
                print(f"      {part.strip()}")

    print("\n" + "=" * 64)
    print("完成")
    print("=" * 64)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
