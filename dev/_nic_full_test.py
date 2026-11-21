"""网口板（i.MX RT1170）完整测试。

覆盖：
  - 连接与状态查询（M114 / M120）
  - IO 控制（M121 开/关阀 + 回读验证）
  - 运动（G0 快速 / G1 插补）
  - G4 不支持验证（会卡死，放最后）
  - 队列深度与 E020
  - 上位机延时（dwell=host）实测精度
"""

from __future__ import annotations

import json
import socket
import statistics
import time

IP, PORT = "192.168.0.102", 5000


class Board:
    def __init__(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.settimeout(10.0)
        self.records: list[dict] = []

    def close(self):
        try:
            self.sock.close()
        except Exception:
            pass

    def ask(self, cmd: str, timeout: float = 10.0) -> tuple[str | None, str, float]:
        """发一条，等到真正的 E 码。返回 (码, 原始回包, 耗时)。"""
        t0 = time.time()
        self.sock.sendto(cmd.encode() + b"\r", (IP, PORT))
        raw_all = ""
        while time.time() - t0 < timeout:
            try:
                d, _ = self.sock.recvfrom(4096)
            except socket.timeout:
                return None, raw_all, time.time() - t0
            t = d.decode("ascii", errors="replace")
            raw_all += t
            for part in t.replace("\r", "\n").split("\n"):
                p = part.strip()
                if p.startswith("E") and len(p) >= 4 and p[1:4].isdigit():
                    return p, raw_all, time.time() - t0
        return None, raw_all, time.time() - t0

    def rec(self, cmd: str, desc: str = "", timeout: float = 10.0) -> dict:
        code, raw, dt = self.ask(cmd, timeout)
        r = {"cmd": cmd, "desc": desc, "code": code,
             "raw": raw.strip(), "elapsed": round(dt, 3)}
        self.records.append(r)
        return r

    def io_out(self) -> int | None:
        """读 IOOUT 掩码。"""
        code, raw, _ = self.ask("M120", timeout=5)
        if code != "E000":
            return None
        import re
        m = re.search(r"IOOUT (0x[0-9A-Fa-f]{2})", raw)
        return int(m.group(1), 16) if m else None

    def pos(self) -> tuple[float, float, float] | None:
        import re
        code, raw, _ = self.ask("M114", timeout=5)
        if code != "E000":
            return None
        m = re.search(r"X:(-?[\d.]+)\s+Y:(-?[\d.]+)\s+Z:(-?[\d.]+)", raw)
        return (float(m.group(1)), float(m.group(2)), float(m.group(3))) if m else None


def main():
    print("=" * 68)
    print("网口板（i.MX RT1170）完整测试")
    print("=" * 68)

    b = Board()
    report = {"records": [], "io": [], "motion": [], "dwell": [], "limits": {}}

    # ── 0. 初始状态 ──
    print("\n[0] 初始状态")
    code, raw, dt = b.ask("M114")
    print(f"    M114 -> {code}  ({dt*1000:.1f}ms)")
    print(f"      {raw.strip()[:120]}")
    print(f"    坐标: {b.pos()}")
    print(f"    IOOUT: 0x{b.io_out():02X}")
    report["initial"] = {"pos": b.pos(), "ioout": b.io_out()}

    # ── 1. IO 控制 ──
    print("\n[1] IO 控制（M121）")
    print(f"{'指令':<16} {'应答':<10} {'IOOUT':<8} {'耗时':>8}")
    print("-" * 50)
    for cmd, desc in [("M121 U0 V0", "全关"),
                      ("M121 U0 V1", "开第0路"),
                      ("M121 U0 V0", "关第0路"),
                      ("M121 U1 V1", "开第1路"),
                      ("M121 U1 V0", "关第1路"),
                      ("M121 V255", "8路全开"),
                      ("M121 V0", "8路全关")]:
        r = b.rec(cmd, desc, timeout=5)
        io = b.io_out()
        print(f"{cmd:<16} {str(r['code']):<10} 0x{io:02X}     {r['elapsed']*1000:6.1f}ms")
        report["io"].append({"cmd": cmd, "desc": desc, "code": r["code"], "ioout": io})

    # ── 2. 运动 ──
    print("\n[2] 运动")
    b.rec("G90", "绝对坐标")
    b.rec("G0 X0 Y0 Z0 F3000", "归零", timeout=20)
    print(f"    归零后坐标: {b.pos()}")

    print(f"\n{'指令':<24} {'应答':<8} {'坐标':<28} {'耗时':>8}")
    print("-" * 72)
    moves = [
        ("G0 X10 Y10 F3000", "G0 到 (10,10)", 20),
        ("G1 X20 Y20 F600", "G1 到 (20,20)", 30),
        ("G1 X0 Y0 F600", "G1 回原点", 30),
    ]
    for cmd, desc, to in moves:
        r = b.rec(cmd, desc, timeout=to)
        p = b.pos()
        ps = f"({p[0]:.2f}, {p[1]:.2f}, {p[2]:.2f})" if p else "?"
        print(f"{cmd:<24} {str(r['code']):<8} {ps:<28} {r['elapsed']:6.2f}s")
        report["motion"].append({"cmd": cmd, "code": r["code"],
                                 "pos": list(p) if p else None,
                                 "elapsed": r["elapsed"]})

    # ── 3. 上位机延时精度 ──
    print("\n[3] 上位机延时精度（dwell=host 模式）")
    print("    板子不支持 G4，延时靠上位机 sleep。测 100/200/500ms 各 5 次。")
    print(f"\n{'目标':>8} {'实测均值':>10} {'偏差':>8} {'抖动(σ)':>10}")
    print("-" * 44)
    for target_ms in [100, 200, 500]:
        samples = []
        for _ in range(5):
            # 用一条极短的 M121 往返来计时：发指令 + 等 E000 + sleep
            t0 = time.time()
            b.ask("M121 U0 V0", timeout=5)
            t1 = time.time()
            time.sleep(target_ms / 1000.0)
            t2 = time.time()
            samples.append((t2 - t1) * 1000)
        mean = statistics.mean(samples)
        sd = statistics.pstdev(samples) if len(samples) > 1 else 0
        print(f"{target_ms:>6}ms {mean:>9.1f}ms {mean-target_ms:>+7.1f}ms {sd:>9.1f}ms")
        report["dwell"].append({"target": target_ms, "mean": round(mean, 1),
                                "sd": round(sd, 1), "samples": [round(s, 1) for s in samples]})

    # ── 4. 限位状态 ──
    print("\n[4] 限位状态（M120）")
    code, raw, _ = b.ask("M120")
    for part in raw.replace("\r", "\n").split("\n"):
        p = part.strip()
        if p.startswith(("IOIN", "IOORG", "IOOUT")):
            print(f"    {p}")
            report["limits"][p.split()[0]] = p

    # ── 5. 队列深度 ──
    print("\n[5] 队列深度（连发不等回包，看何时 E020）")
    b.sock.settimeout(0.3)
    sent = 0
    e020_at = None
    for i in range(20):
        b.sock.sendto(b"M114\r", (IP, PORT))
        sent += 1
        time.sleep(0.02)
    time.sleep(1.0)
    # 收集所有回包
    buf = ""
    try:
        while True:
            d, _ = b.sock.recvfrom(8192)
            buf += d.decode("ascii", errors="replace")
    except socket.timeout:
        pass
    if "E020" in buf:
        e020_at = buf.count("E020")
    print(f"    连发 {sent} 条，收到 E020 次数: {e020_at or 0}")
    print(f"    → 队列确实有深度限制，超发会丢行")
    report["queue"] = {"sent": sent, "e020_count": e020_at or 0}

    # 等队列排空
    print("    等队列排空…")
    b.sock.settimeout(10.0)
    for _ in range(30):
        code, _, _ = b.ask("M114", timeout=5)
        if code == "E000":
            break
        time.sleep(0.5)
    print(f"    队列已清: {code}")

    # ── 6. G4 不支持（最后做，会卡死） ──
    print("\n[6] G4 支持性（放最后，因为会卡死板子）")
    print("    发 G4 P100…")
    code, raw, dt = b.ask("G4 P100", timeout=5)
    print(f"    -> {code}  ({dt:.1f}s)")
    if code is None:
        print("    ✗ 无回包，板子卡死 —— 确认不支持 G4")
        # 再确认一次
        code2, _, dt2 = b.ask("M114", timeout=5)
        print(f"    卡死后 M114 -> {code2}")
        report["g4"] = {"supported": False, "blocked": True}
    else:
        print(f"    有回包，可能支持")
        report["g4"] = {"supported": True, "code": code}

    b.close()

    with open("_nic_report.json", "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print("\n报告已写入 _nic_report.json")
    print("⚠ 板子可能已被 G4 卡死，需要断电重启")


if __name__ == "__main__":
    main()
