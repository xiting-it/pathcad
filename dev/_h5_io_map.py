"""H5 IO 通道映射与 G4 延时测试（干净版）。

修掉上一版的两个问题：
  1. 每条指令前先 drain，避免读到上一条的 echo 残留
  2. 读输出用 M120 U<n>（不是 I，I 是输入）
"""

from __future__ import annotations

import socket
import time

IP = "192.168.0.102"
PORT = 5000
LOCAL = "192.168.0.100"
CR = b"\r"


class H5:
    def __init__(self) -> None:
        self.s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.s.bind((LOCAL, 0))
        self.s.settimeout(2.0)

    def close(self) -> None:
        self.s.close()

    def drain(self, wait: float = 0.25) -> None:
        end = time.time() + wait
        while time.time() < end:
            try:
                self.s.recvfrom(4096)
            except socket.timeout:
                break

    def ask(self, line: str, timeout: float = 4.0) -> tuple[list[str], float]:
        self.drain(0.2)                      # ← 关键：先清掉残留
        self.s.sendto(line.encode() + CR, (IP, PORT))
        rows: list[str] = []
        t0 = time.perf_counter()
        end = t0 + timeout
        while time.perf_counter() < end:
            try:
                data, _ = self.s.recvfrom(4096)
            except socket.timeout:
                break
            for part in data.decode("ascii", "replace").split("\r"):
                part = part.strip()
                if part:
                    rows.append(part)
            if any(len(r) >= 4 and r[0] in "EB" and r[1:4].isdigit() for r in rows):
                break
        return rows, time.perf_counter() - t0


def datalines(rows: list[str]) -> list[str]:
    out = []
    for r in rows:
        if r.startswith("queued") or r in ("ok",):
            continue
        if len(r) >= 4 and r[0] in "EB" and r[1:4].isdigit():
            continue
        out.append(r)
    return out


def code(rows: list[str]) -> str:
    for r in rows:
        if len(r) >= 4 and r[0] in "EB" and r[1:4].isdigit():
            return r
    return "无"


def main() -> None:
    h = H5()
    print("=" * 60)
    print(" 一、IO 输出通道映射")
    print("=" * 60)

    rows, _ = h.ask("M120")
    print(f"初始: {' | '.join(datalines(rows))}")

    print("\n逐路点亮，用 M120 U<n> 读回：")
    working = []
    for ch in range(8):
        h.ask(f"M121 V0")                       # 先全关
        rows, _ = h.ask(f"M121 U{ch} V1")
        c1 = code(rows)
        rows2, _ = h.ask(f"M120 U{ch}")
        back = " ".join(datalines(rows2))
        h.ask(f"M121 U{ch} V0")
        ok = "=1" in back
        if ok:
            working.append(ch)
        print(f"  U{ch}: 写{c1:5s} 读回 {back or '(空)':22s} {'✓' if ok else '✗ 不响应'}")

    print(f"\n实际可用输出通道: {working if working else '无'}")

    print("\n掩码测试：")
    for mask in (1, 3, 15, 255):
        h.ask("M121 V0")
        rows, _ = h.ask(f"M121 V{mask}")
        rows2, _ = h.ask("M120")
        state = " ".join(datalines(rows2))
        print(f"  M121 V{mask:<3d} → {state}   ({code(rows)})")
        h.ask("M121 V0")

    print("\n" + "=" * 60)
    print(" 二、G4 延时精度")
    print("=" * 60)
    for ms in (0, 100, 300, 500):
        rows, dt = h.ask(f"G4 P{ms}", timeout=8)
        extra = dt * 1000 - 45          # 45ms 是实测的空指令往返本底
        print(f"  G4 P{ms:<4d} 回 {code(rows):5s} 往返 {dt*1000:6.0f} ms  "
              f"扣除本底后 ≈{extra:5.0f} ms")

    print("\n" + "=" * 60)
    print(" 三、空指令往返本底（对照组）")
    print("=" * 60)
    base = []
    for _ in range(3):
        rows, dt = h.ask("M114")
        base.append(dt * 1000)
    print(f"  M114 三次: {[round(b) for b in base]} ms")

    h.ask("M121 V0")
    h.close()
    print("\n结束，IO 已全关。")


if __name__ == "__main__":
    main()
