"""H5 板完整通信测试（四电机已接）。

分四个阶段，逐步加码，每一步都先确认上一步正常：
  阶段 0  链路检查
  阶段 1  只读查询（M114/M120/M92/M154）—— 不动电机
  阶段 2  单轴微动（每轴 2mm，慢速）—— 确认方向和限位
  阶段 3  IO 读写（M121/M120）
  阶段 4  G4 延时精度（H5 比旧板多的能力）

用法：
    python dev/_h5_test.py            # 跑全部阶段
    python dev/_h5_test.py --readonly # 只跑到阶段 1
    python dev/_h5_test.py --axis X   # 只测某个轴
"""

from __future__ import annotations

import argparse
import socket
import sys
import time

IP = "192.168.0.102"
PORT = 5000
LOCAL_IP = "192.168.0.100"
CR = b"\r"


# ── 通信 ────────────────────────────────────────────────


class H5:
    def __init__(self, local_ip: str = LOCAL_IP) -> None:
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            self.sock.bind((local_ip, 0))
            self.local = local_ip
        except OSError:
            self.sock.bind(("", 0))
            self.local = "0.0.0.0"
        self.sock.settimeout(3.0)

    def close(self) -> None:
        self.sock.close()

    def drain(self, wait: float = 0.3) -> None:
        end = time.time() + wait
        while time.time() < end:
            try:
                self.sock.recvfrom(4096)
            except socket.timeout:
                break

    def ask(self, line: str, timeout: float = 4.0) -> tuple[list[str], float]:
        """发一行，返回 (所有回包行, 耗时秒)。"""
        self.sock.sendto(line.encode("ascii") + CR, (IP, PORT))
        rows: list[str] = []
        t0 = time.perf_counter()
        end = t0 + timeout
        while time.perf_counter() < end:
            try:
                data, _ = self.sock.recvfrom(4096)
            except socket.timeout:
                break
            for part in data.decode("ascii", "replace").split("\r"):
                part = part.strip()
                if part:
                    rows.append(part)
            if any(_is_result(r) for r in rows):
                break
        return rows, time.perf_counter() - t0


def _is_result(line: str) -> bool:
    """是不是最终结果码（E000 / B000 之类）。"""
    return len(line) >= 4 and line[0] in ("E", "B") and line[1:4].isdigit()


def result_code(rows: list[str]) -> str | None:
    for r in rows:
        if _is_result(r):
            return r
    return None


def payload(rows: list[str]) -> list[str]:
    """去掉 queued#N / echo / 结果码，只留数据行。"""
    out = []
    for r in rows:
        if r.startswith("queued"):
            continue
        if _is_result(r):
            continue
        out.append(r)
    return out


# ── 显示 ────────────────────────────────────────────────


def show(rows: list[str], dt: float, indent: str = "    ") -> None:
    if not rows:
        print(f"{indent}[无回包] {dt*1000:.0f} ms")
        return
    for r in payload(rows):
        print(f"{indent}{r}")
    code = result_code(rows)
    flag = "[OK]  " if code and code.endswith("000") else "[??]  "
    print(f"{indent}{flag}{code or '无结果码'}   {dt*1000:.0f} ms")


def header(title: str) -> None:
    print(f"\n{'='*58}\n {title}\n{'='*58}")


# ── 阶段 ────────────────────────────────────────────────


def phase_link() -> bool:
    header("阶段 0 · 链路检查")
    import subprocess

    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "Get-NetAdapter | Where-Object {$_.Name -like '*以太网 2*'} | "
             "Select-Object -ExpandProperty Status"],
            capture_output=True, text=True, timeout=15,
        )
        st = (r.stdout or "").strip()
        print(f"  以太网 2 状态: {st or '未知'}")
        if "Up" not in st:
            print("  [!!] 网卡未连接 —— 检查网线和板子供电")
            return False
    except Exception as e:
        print(f"  [警告] 查网卡失败: {e}")

    h = H5()
    h.drain(0.3)
    rows, dt = h.ask("M114", timeout=3)
    h.close()
    if not rows:
        print("  [!!] 板子无回包 —— 网线/供电/IP 任一处不对")
        return False
    print("  板子有回包，链路正常")
    return True


def phase_readonly(h: H5) -> None:
    header("阶段 1 · 只读查询（不动电机）")

    queries = [
        ("M114", "工作坐标（四轴）"),
        ("M120", "三组 IO 各 8 路"),
        ("M92", "丝杆导程"),
        ("M203", "G0 快速速度"),
        ("M154", "详细位置（走 SPI，需排队）"),
    ]
    for cmd, desc in queries:
        print(f"\n  >>> {cmd}  —— {desc}")
        rows, dt = h.ask(cmd)
        show(rows, dt)


def phase_motion(h: H5, axes: str, dist: float, feed: int) -> None:
    header(f"阶段 2 · 单轴微动（每轴 {dist}mm，F{feed}）")
    print("  注意：这会让电机真的动。先看方向对不对，再加大。\n")

    rows, dt = h.ask("M114")
    print("  动之前：")
    show(rows, dt)

    for ax in axes:
        print(f"\n  >>> G1 {ax}{dist} F{feed}")
        rows, dt = h.ask(f"G1 {ax}{dist} F{feed}", timeout=15)
        show(rows, dt)
        time.sleep(0.3)
        rows, dt = h.ask("M114")
        print("      位置：", " | ".join(payload(rows)) or "(无)")

    print("\n  >>> M400 等待运动停止")
    rows, dt = h.ask("M400", timeout=30)
    show(rows, dt)


def phase_io(h: H5) -> None:
    header("阶段 3 · IO 读写")

    print("\n  >>> M120 读初始状态")
    rows, dt = h.ask("M120")
    show(rows, dt)

    for cmd in ("M121 U0 V1", "M120 I0", "M121 U0 V0", "M120 I0"):
        print(f"\n  >>> {cmd}")
        rows, dt = h.ask(cmd)
        show(rows, dt)

    print("\n  >>> M121 V255 全开 → 读 → 全关")
    for cmd in ("M121 V255", "M120", "M121 V0", "M120"):
        print(f"\n  >>> {cmd}")
        rows, dt = h.ask(cmd)
        show(rows, dt)


def phase_dwell(h: H5) -> None:
    header("阶段 4 · G4 延时精度（H5 新能力）")

    for ms in (100, 300, 500):
        cmd = f"G4 P{ms}"
        print(f"\n  >>> {cmd}   期望 ≈{ms} ms")
        rows, dt = h.ask(cmd)
        show(rows, dt)
        if rows and result_code(rows):
            err = abs(dt * 1000 - ms - 30)  # 30ms 是往返本底
            print(f"      偏差估算 {err:.0f} ms（含网络往返）")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--readonly", action="store_true", help="只跑到只读阶段")
    ap.add_argument("--axis", default="XYZ", help="要测的轴，默认 XYZ")
    ap.add_argument("--dist", type=float, default=2.0, help="每轴移动距离 mm")
    ap.add_argument("--feed", type=int, default=600, help="进给 mm/min")
    ap.add_argument("--skip-motion", action="store_true")
    args = ap.parse_args()

    if not phase_link():
        print("\n链路不通，先修物理连接。")
        return 1

    h = H5()
    print(f"\n本机绑定 {h.local}:{h.sock.getsockname()[1]} → 板子 {IP}:{PORT}")
    h.drain(0.3)

    try:
        phase_readonly(h)
        if args.readonly:
            print("\n(只读模式，结束)")
            return 0
        if not args.skip_motion:
            phase_motion(h, args.axis, args.dist, args.feed)
        phase_io(h)
        phase_dwell(h)
    finally:
        h.close()

    print(f"\n{'='*58}\n 测试完成\n{'='*58}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
