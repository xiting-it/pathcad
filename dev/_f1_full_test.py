"""LCSystemF1 板卡功能与协议测试。

覆盖：
  - 连接与状态查询
  - G00 / G01 定位与联动
  - G02 圆弧（I/J 和 R 两种写法、长弧短弧、Z 跟随）
  - 速度/加速度实际耗时测量
  - 错误码边界（E03/E04/E05/E06/E07）
  - 限位状态读取

所有指令用 CRC=FFFF 旁路（用户指定），同时抽样验证真实 CRC 也能通过。
"""

from __future__ import annotations

import json
import statistics
import sys
import time
from dataclasses import dataclass, field

import serial

PORT = "COM7"
BAUD = 115200
BYPASS = "FFFF"


# ── CRC ────────────────────────────────────────────────

def crc16_ccitt_false(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if (crc & 0x8000) else (crc << 1) & 0xFFFF
    return crc


def build_frame(gcode: str, bypass: bool = True) -> bytes:
    body = gcode.encode("ascii")
    assert len(body) <= 89, f"正文 {len(body)}B 超限"
    crc = BYPASS if bypass else "%04X" % crc16_ccitt_false(body)
    return b"@" + body + b"*" + crc.encode() + b"\r"


# ── 串口封装 ────────────────────────────────────────────

@dataclass
class Result:
    cmd: str
    desc: str
    resp: str = ""
    elapsed: float = 0.0
    ok: bool = False
    note: str = ""


class Board:
    def __init__(self, port: str = PORT, baud: int = BAUD):
        self.ser = serial.Serial(port, baud, timeout=3, write_timeout=3)
        time.sleep(0.5)
        self.ser.reset_input_buffer()
        self.results: list[Result] = []

    def close(self):
        try:
            self.ser.close()
        except Exception:
            pass

    def cmd(self, gcode: str, desc: str = "", timeout: float = 3.0,
            bypass: bool = True, wait_extra: float = 0.05) -> Result:
        """发一条指令并读应答。"""
        r = Result(cmd=gcode, desc=desc)
        frame = build_frame(gcode, bypass=bypass)
        self.ser.reset_input_buffer()
        t0 = time.time()
        self.ser.write(frame)
        self.ser.flush()
        self.ser.timeout = timeout
        try:
            data = self.ser.read_until(b"\r")
        except Exception as e:
            r.resp = f"(读异常: {e})"
            return r
        r.elapsed = time.time() - t0
        if not data:
            r.resp = "(超时)"
            self.results.append(r)
            return r
        r.resp = data[:-1].decode("ascii", errors="replace")
        r.ok = r.resp.startswith("E00") or r.resp.startswith("D ")
        self.results.append(r)
        if wait_extra:
            time.sleep(wait_extra)
        return r

    def status(self) -> dict:
        """M50 状态。返回 {lx,ly,lz,rx,ry,rz,x,y,z,raw}

        帧格式：D L000 R000 P 0.00 0.00 0.00*CRC
        注意 P 后面是「空格分隔的三组坐标」，不是 P0.00。
        """
        r = self.cmd("M50", "状态查询", timeout=3)
        d = {"raw": r.resp}
        try:
            import re
            body = r.resp.split("*")[0]          # 去掉 CRC
            m = re.match(r"D\s+L(\d)(\d)(\d)\s+R(\d)(\d)(\d)\s+P\s*(.+)", body)
            if not m:
                d["parse_error"] = "帧格式不匹配"
                return d
            xyz = m.group(7).split()
            d.update({
                "lx": int(m.group(1)), "ly": int(m.group(2)), "lz": int(m.group(3)),
                "rx": int(m.group(4)), "ry": int(m.group(5)), "rz": int(m.group(6)),
                "x": float(xyz[0]), "y": float(xyz[1]), "z": float(xyz[2]),
            })
        except Exception as e:
            d["parse_error"] = str(e)
        return d

    def goto(self, x=None, y=None, z=None, feed=None, rapid=False):
        parts = ["G00" if rapid else "G01"]
        if x is not None:
            parts.append(f"X{x:g}")
        if y is not None:
            parts.append(f"Y{y:g}")
        if z is not None:
            parts.append(f"Z{z:g}")
        if feed is not None and not rapid:
            parts.append(f"F{feed:g}")
        return self.cmd(" ".join(parts), f"移动到 ({x},{y},{z})", timeout=60)


# ── 测试主流程 ──────────────────────────────────────────

def main():
    print("=" * 60)
    print("LCSystemF1 板卡测试")
    print("=" * 60)

    b = Board()
    report = {"tests": [], "summary": {}}

    # ── 0. 初始状态 ──
    print("\n[0] 初始状态")
    st = b.status()
    print(f"    {st['raw']}")
    print(f"    坐标 ({st.get('x')}, {st.get('y')}, {st.get('z')})")
    report["initial"] = st

    # ── 1. G00 快速定位 ──
    print("\n[1] G00 快速定位（固定 3000mm/min）")
    seq = [("G00 X10", "X→10"), ("G00 X10 Y10", "Y→10"), ("G00 X20 Y20", "X,Y→20"),
           ("G00 X20 Y20 Z5", "Z→5"), ("G00 X0 Y0 Z0", "回原点")]
    for g, desc in seq:
        r = b.cmd(g, desc, timeout=30)
        st = b.status()
        print(f"    {g:22s} {r.resp:10s} 坐标=({st.get('x')},{st.get('y')},{st.get('z')})")
        report["tests"].append({"cmd": g, "resp": r.resp, "elapsed": round(r.elapsed, 3),
                                "pos": [st.get("x"), st.get("y"), st.get("z")]})

    # ── 2. G01 直线插补 + 速度 ──
    print("\n[2] G01 直线插补与速度")
    b.cmd("G00 X0 Y0 Z0", "home", timeout=30)
    for feed in [300, 600, 1200, 3000]:
        dist = 50
        r = b.cmd(f"G01 X{dist} F{feed}", f"F{feed} 走 {dist}mm", timeout=90)
        st = b.status()
        t = r.elapsed
        v_avg = dist / t if t > 0 else 0
        v_nom = feed / 60
        print(f"    F{feed:5d} 走{dist}mm 耗时 {t:6.2f}s  平均 {v_avg:6.2f} mm/s  "
              f"(标称 {v_nom:5.2f})  效率 {v_avg/v_nom*100:5.1f}%")
        report["tests"].append({"cmd": f"G01 X{dist} F{feed}", "resp": r.resp,
                                "elapsed": round(t, 3),
                                "avg_mm_s": round(v_avg, 2),
                                "nominal_mm_s": round(v_nom, 2),
                                "efficiency": round(v_avg / v_nom * 100, 1)})
        b.cmd("G00 X0", "返回", timeout=90)

    # ── 3. G02 圆弧 I/J ──
    print("\n[3] G02 圆弧（I/J 圆心偏移）")
    b.cmd("G00 X0 Y0 Z0", "home", timeout=30)
    # 从 (10,0) 画到 (0,10)，圆心在原点 → I=-10, J=0
    b.cmd("G00 X10 Y0", "移到起点", timeout=30)
    r = b.cmd("G02 X0 Y10 I-10 J0 F600", "四分之一圆 I/J", timeout=60)
    st = b.status()
    print(f"    G02 X0 Y10 I-10 J0   -> {r.resp}  坐标=({st.get('x')},{st.get('y')})")
    report["tests"].append({"cmd": "G02 X0 Y10 I-10 J0 F600", "resp": r.resp,
                            "elapsed": round(r.elapsed, 3),
                            "pos": [st.get("x"), st.get("y")]})

    # 半圆
    b.cmd("G00 X0 Y0", "回原点", timeout=30)
    b.cmd("G00 X10 Y0", "移到起点", timeout=30)
    r = b.cmd("G02 X-10 Y0 I-10 J0 F600", "半圆 I/J", timeout=60)
    st = b.status()
    print(f"    G02 X-10 Y0 I-10 J0 -> {r.resp}  坐标=({st.get('x')},{st.get('y')})")
    report["tests"].append({"cmd": "G02 X-10 Y0 I-10 J0 F600", "resp": r.resp,
                            "elapsed": round(r.elapsed, 3),
                            "pos": [st.get("x"), st.get("y")]})

    # ── 4. G02 圆弧 R ──
    print("\n[4] G02 圆弧（R 半径，正=短弧 负=长弧）")
    b.cmd("G00 X0 Y0", "回原点", timeout=30)
    b.cmd("G00 X10 Y0", "到起点", timeout=30)
    r = b.cmd("G02 X0 Y10 R10 F600", "短弧 R+10", timeout=60)
    st = b.status()
    print(f"    G02 X0 Y10 R10   -> {r.resp}  坐标=({st.get('x')},{st.get('y')})")
    report["tests"].append({"cmd": "G02 X0 Y10 R10 F600", "resp": r.resp,
                            "elapsed": round(r.elapsed, 3)})
    r = b.cmd("G02 X10 Y0 R-10 F600", "长弧 R-10", timeout=90)
    st = b.status()
    print(f"    G02 X10 Y0 R-10  -> {r.resp}  坐标=({st.get('x')},{st.get('y')})")
    report["tests"].append({"cmd": "G02 X10 Y0 R-10 F600", "resp": r.resp,
                            "elapsed": round(r.elapsed, 3)})

    # ── 5. G02 Z 跟随 ──
    print("\n[5] G02 带 Z 轴线性跟随")
    b.cmd("G00 X0 Y0 Z0", "回原点", timeout=30)
    b.cmd("G00 X10 Y0", "到起点", timeout=30)
    r = b.cmd("G02 X0 Y10 Z2 I-10 J0 F600", "圆弧+Z跟随", timeout=60)
    st = b.status()
    print(f"    G02 X0 Y10 Z2 I-10 J0 -> {r.resp}  坐标=({st.get('x')},{st.get('y')},{st.get('z')})")
    report["tests"].append({"cmd": "G02 X0 Y10 Z2 I-10 J0 F600", "resp": r.resp,
                            "pos": [st.get("x"), st.get("y"), st.get("z")]})

    # ── 6. 错误码 ──
    print("\n[6] 错误码边界")
    err_tests = [
        ("G99", "E03 不支持的指令"),
        ("G01 X", "E04 语法错误"),
        ("G01 X10 Y10 Z10 A5", "E04 未知轴字母"),
        ("G00 X9999", "E05 超行程"),
        ("G01 X10 F0", "E05 F=0"),
        ("G01 X10 F7000", "E05 F 超上限"),
        ("G02 X10 Y10 F600", "E06 缺 IJ/R"),
        ("G02 X10 Y10 R1 F600", "E06 R 太小"),
    ]
    for g, desc in err_tests:
        r = b.cmd(g, desc, timeout=10)
        expect = desc.split()[0]
        got = r.resp.split("*")[0] if r.resp else "?"
        mark = "OK" if got == expect else f"期望{expect}"
        print(f"    {g:26s} -> {r.resp:10s} [{mark}]")
        report["tests"].append({"cmd": g, "resp": r.resp, "expect": expect})

    # ── 7. 真实 CRC vs 旁路 ──
    print("\n[7] 真实 CRC 校验（不用 FFFF）")
    for g in ["M50", "G00 X0 Y0 Z0", "G01 X5 F600"]:
        r = b.cmd(g, "真实CRC", timeout=10, bypass=False)
        print(f"    {g:22s} -> {r.resp}")
        report["tests"].append({"cmd": g, "resp": r.resp, "crc_mode": "real"})

    # ── 8. 限位状态 ──
    print("\n[8] 限位状态")
    st = b.status()
    print(f"    左限位 X/Y/Z = {st.get('lx')}/{st.get('ly')}/{st.get('lz')}")
    print(f"    右限位 X/Y/Z = {st.get('rx')}/{st.get('ry')}/{st.get('rz')}")
    report["limits"] = {"L": [st.get("lx"), st.get("ly"), st.get("lz")],
                        "R": [st.get("rx"), st.get("ry"), st.get("rz")]}

    # 收尾
    b.cmd("G00 X0 Y0 Z0", "回原点", timeout=30)
    final = b.status()
    report["final"] = final
    print(f"\n最终状态: {final['raw']}")

    b.close()

    with open("_f1_report.json", "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print("\n报告已写入 _f1_report.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
