"""批量发送性能对比：batch_size=1 vs 6。

同一份 G-code，分别用两种窗口大小下发，比较总耗时和错误数。
"""

from __future__ import annotations

import json
import time
import urllib.request

BASE = "http://127.0.0.1:8765"
IP, PORT = "192.168.0.102", 5000


def get(p):
    return json.load(urllib.request.urlopen(BASE + p))


def post(p, body, timeout=400):
    req = urllib.request.Request(
        BASE + p, data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    return json.load(urllib.request.urlopen(req, timeout=timeout))


def run_job(gcode, delays, batch, label):
    r = post("/api/link/connect", {"ip": IP, "port": PORT})
    if not r["ok"]:
        print(f"    连接失败: {r['message']}")
        return None

    t0 = time.time()
    r = post("/api/job/start", {
        "gcode": gcode, "ip": IP, "port": PORT,
        "delays": delays, "batch_size": batch,
    })
    if not r["ok"]:
        print(f"    启动失败: {r['message']}")
        return None
    print(f"    {r['message']}")

    while True:
        time.sleep(0.5)
        s = get(f"/api/job/status?ip={IP}&port={PORT}")
        if not s["running"]:
            break
        if time.time() - t0 > 600:
            post("/api/job/stop", {"ip": IP, "port": PORT})
            print("    超时")
            break

    elapsed = time.time() - t0
    print(f"    {label}: {s['done']}/{s['total']} 行, {s['errors']} 错, 耗时 {elapsed:.1f}s")
    if s["log"]:
        for lg in s["log"][:3]:
            print(f"      {lg}")
    return {"done": s["done"], "total": s["total"],
            "errors": s["errors"], "elapsed": round(elapsed, 1)}


def main():
    print("=" * 68)
    print("批量发送性能对比")
    print("=" * 68)

    # 构造一个 8mm 方块 + 格栅
    fr = post("/api/fill", {
        "source": {"type": "rect", "x": 0, "y": 0, "w": 8, "h": 8},
        "pattern": "grid", "spacing": 2.0, "inset": 0.4, "min_len": 0.5,
        "sort": True, "merge": False, "stagger": False, "outline": True,
    })
    strokes = []
    for i, p in enumerate(fr["paths"]):
        strokes.append({
            "id": f"s{i}",
            "points": [{"x": x, "y": y} for x, y in p],
            "closed": True, "kind": "contour",
            "start": {"io": 0, "level": 1, "delay_ms": 150},
            "end": {"io": 0, "level": 0, "delay_ms": 100},
            "source": None,
        })
    proj = {
        "version": 1, "name": "perf",
        "machine": {"name": "lc", "travel_x": 200, "travel_y": 200, "travel_z": 50,
                    "soft_limit": True, "has_home_switch": False,
                    "feed_default": 600, "feed_rapid": 3000, "accel": 300},
        "process": {"rise_ms": 150, "tail_ms": 100, "lift_z": 0.3, "lift_feed": 3000,
                    "line_width": 0.45, "layer_height": 0.4, "min_segment_len": 0.5},
        "layers": [{"id": "L1", "z": 0, "repeat": 1, "z_step": 0.4,
                    "overrides": {}, "visible": True, "strokes": strokes}],
        "calibration_profile_id": None,
    }
    c = post("/api/compile", {"project": proj, "dialect": "lc"})
    n_lines = c["stats"]["lines"]
    n_strokes = c["stats"]["strokes"]
    print(f"\n工程: {n_strokes} 笔画, {n_lines} 行")
    print(f"上位机延时总计: {c['stats']['host_dwell_seconds']}s")

    results = {}

    print(f"\n{'='*68}")
    print("[A] batch_size=1（逐行一发一等）")
    print("=" * 68)
    results["batch1"] = run_job(c["gcode"], c["delays"], 1, "batch=1")

    print(f"\n{'='*68}")
    print("[B] batch_size=6（一包 6 条）")
    print("=" * 68)
    results["batch6"] = run_job(c["gcode"], c["delays"], 6, "batch=6")

    print(f"\n{'='*68}")
    print("对比")
    print("=" * 68)
    a, b = results.get("batch1"), results.get("batch6")
    if a and b:
        print(f"  batch=1: {a['elapsed']:>6.1f}s  ({a['elapsed']/a['total']*1000:.0f}ms/行)  {a['errors']} 错")
        print(f"  batch=6: {b['elapsed']:>6.1f}s  ({b['elapsed']/b['total']*1000:.0f}ms/行)  {b['errors']} 错")
        if b["elapsed"] > 0:
            print(f"  加速比: {a['elapsed']/b['elapsed']:.2f}×")
    with open("_batch_perf.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)


if __name__ == "__main__":
    main()
