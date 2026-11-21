"""G4 参数单位到底是毫秒还是秒？—— 用一个极小值计时判定。

思路：
  发 G4 P1，精确测到 E000 的时间。
    · 若 ≈1ms   → P 是毫秒（文档对）
    · 若 ≈1000ms → P 是秒（文档错 1000 倍）
    · 若 ≈0.1s   → P 是 0.1 秒
  P1 是最小值，无论单位是什么都不会卡太久。
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

    def close(self) -> None:
        self.s.close()

    def drain(self, wait: float = 0.2) -> None:
        self.s.settimeout(wait)
        end = time.time() + wait
        try:
            while time.time() < end:
                self.s.recvfrom(4096)
        except socket.timeout:
            pass

    def ask(self, line: str, timeout: float = 60.0) -> tuple[str, float, list[str]]:
        """发一行，返回 (结果码, 耗时秒, 原始行)。"""
        self.drain(0.2)
        self.s.settimeout(min(timeout, 5.0))
        self.s.sendto(line.encode() + CR, (IP, PORT))
        t0 = time.perf_counter()
        rows: list[str] = []
        while time.perf_counter() - t0 < timeout:
            try:
                data, _ = self.s.recvfrom(4096)
            except socket.timeout:
                continue
            for part in data.decode("ascii", "replace").split("\r"):
                part = part.strip()
                if not part:
                    continue
                rows.append(part)
                if len(part) >= 4 and part[0] in "EB" and part[1:4].isdigit():
                    return part, time.perf_counter() - t0, rows
        return "超时", time.perf_counter() - t0, rows


def main() -> None:
    h = H5()

    print("=" * 66)
    print(" 先确认队列是空的")
    print("=" * 66)
    for cmd in ("M400", "M114"):
        code, dt, _ = h.ask(cmd, timeout=10)
        print(f"  {cmd:16s} → {code:6s} {dt*1000:7.0f} ms")

    print("\n" + "=" * 66)
    print(" 关键：G4 P1 计时（P1 是最小值，无论如何都不会卡太久）")
    print("=" * 66)
    base, base_dt, _ = h.ask("M114", timeout=10)
    base_ms = base_dt * 1000
    print(f"  基准（M114 往返）：{base_ms:.0f} ms")

    for arg in ("G4 P1", "G4 P10"):
        print(f"\n  >>> {arg}")
        code, dt, rows = h.ask(arg, timeout=120)
        ms = dt * 1000
        print(f"      {code}   实际 {ms:.0f} ms   扣除基准 ≈ {ms - base_ms:.0f} ms")
        if code.endswith("000"):
            raw = ms - base_ms
            print(f"      → 若按毫秒算应 ≈ {arg.split('P')[1]} ms，实际 {raw:.0f} ms")

    print("\n" + "=" * 66)
    print(" 顺带测 S 参数（文档说 S 是秒）")
    print("=" * 66)
    print("\n  >>> G4 S0.1")
    code, dt, rows = h.ask("G4 S0.1", timeout=120)
    print(f"      {code}   实际 {dt*1000:.0f} ms（若 S 是秒，应 ≈100ms）")

    print("\n  >>> M114 确认队列还能用")
    code, dt, _ = h.ask("M114", timeout=10)
    print(f"      {code}   {dt*1000:.0f} ms")

    h.ask("M121 V0")
    h.close()


if __name__ == "__main__":
    main()
