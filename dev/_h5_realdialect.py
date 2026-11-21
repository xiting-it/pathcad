"""真板验证：B/E 双码 + G4 秒单位 + M121 IO。

安全约束：只做 1mm 的微小移动，速度 600mm/min。
"""

from __future__ import annotations

import socket
import time

IP = "192.168.0.102"
PORT = 5000
LOCAL = "192.168.0.100"
CR = b"\r"

REPLY = ("E", "B")


def is_code(t: str) -> bool:
    return len(t) == 4 and t[0] in REPLY and t[1:].isdigit()


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

    def ask(self, line: str, timeout: float = 20.0) -> tuple[str, float, list[str]]:
        self.drain(0.2)
        self.s.settimeout(min(timeout, 3.0))
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
                if is_code(part):
                    return part, time.perf_counter() - t0, rows
        return "超时", time.perf_counter() - t0, rows


def show(label: str, code: str, dt: float, rows: list[str]) -> None:
    data = [r for r in rows if not is_code(r) and not r.startswith("queued") and r != "ok"]
    mark = "OK " if code in ("E000", "B000") else "!! "
    print(f"  {mark}{label:22s} → {code:5s} {dt*1000:7.0f} ms   {' '.join(data)[:56]}")


def main() -> None:
    h = H5()
    print("=" * 72)
    print(" 真板验证（只做 1mm 微动）")
    print("=" * 72)

    print("\n【A】回码字母应该分 B / E")
    for cmd, expect in (("M114", "B"), ("M120", "B"), ("M121 V0", "B")):
        code, dt, rows = h.ask(cmd)
        show(f"{cmd}（应 {expect} 码）", code, dt, rows)

    print("\n【B】G4 秒单位计时（应约等于 P×1000 ms）")
    base_code, base_dt, _ = h.ask("M114")
    base = base_dt * 1000
    print(f"      基准 M114 往返 {base:.0f} ms")
    for p_ms, label in ((0.05, "50ms"), (0.12, "120ms"), (0.25, "250ms")):
        cmd = f"G4 P{p_ms}"
        code, dt, rows = h.ask(cmd, timeout=20)
        net = dt * 1000 - base
        show(f"{cmd}（应≈{label}）", code, dt, rows)
        print(f"        扣除基准 ≈ {net:.0f} ms   偏差 {net - p_ms*1000:+.0f} ms")

    print("\n【C】完整笔画时序：开阀 → G4 → 移动 → 关阀")
    seq = [
        ("M121 U0 V1", 5.0),
        ("G4 P0.12", 5.0),
        ("G1 X1 F600", 15.0),
        ("M121 U0 V0", 5.0),
        ("G4 P0.08", 5.0),
        ("M114", 5.0),
    ]
    t_all = time.perf_counter()
    for cmd, to in seq:
        code, dt, rows = h.ask(cmd, timeout=to)
        show(cmd, code, dt, rows)
    print(f"      整段耗时 {(time.perf_counter()-t_all)*1000:.0f} ms"
          f"（运动 1mm@600 = 100ms + 延时 200ms）")

    print("\n【D】收尾")
    h.ask("M121 V0")
    code, dt, rows = h.ask("M114")
    show("M114 最终位置", code, dt, rows)

    h.close()


if __name__ == "__main__":
    main()
