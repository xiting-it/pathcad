"""H5 板通信探针 —— 只读阶段。

只发查询类指令，不发任何运动指令。确认板子在线、能回包、坐标与 IO 状态可见。
"""

from __future__ import annotations

import socket
import time

IP = "192.168.0.102"
PORT = 5000
CR = b"\r"


def make_sock() -> socket.socket:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("192.168.0.100", 0))  # 绑在以太网那块网卡上，避免走 WLAN
    s.settimeout(3.0)
    return s


def drain(s: socket.socket, wait: float = 0.6) -> list[str]:
    """把这段时间内收到的所有包收干净，按 \\r 切分成行。"""
    out: list[str] = []
    end = time.time() + wait
    while time.time() < end:
        try:
            data, _ = s.recvfrom(4096)
        except socket.timeout:
            break
        text = data.decode("ascii", "replace")
        for line in text.split("\r"):
            line = line.strip()
            if line:
                out.append(line)
    return out


def ask(s: socket.socket, line: str, timeout: float = 3.0) -> list[str]:
    """发一条，收它引起的所有回包。"""
    s.sendto(line.encode("ascii") + CR, (IP, PORT))
    rows: list[str] = []
    end = time.time() + timeout
    while time.time() < end:
        try:
            data, _ = s.recvfrom(4096)
        except socket.timeout:
            break
        text = data.decode("ascii", "replace")
        for part in text.split("\r"):
            part = part.strip()
            if part:
                rows.append(part)
        # 拿到最终结果码就可以停
        if any(r[:1] in ("E", "B") and len(r) >= 4 and r[1:4].isdigit() for r in rows):
            break
    return rows


def main() -> None:
    print(f"目标 {IP}:{PORT}\n")
    s = make_sock()

    # 先清掉可能残留的回包
    drain(s, 0.4)

    tests = [
        ("M114", "读工作坐标（四轴）"),
        ("M120", "读三组 IO 各 8 路"),
        ("M92", "查丝杆导程"),
    ]

    for cmd, desc in tests:
        print(f">>> {cmd:12s} {desc}")
        t0 = time.perf_counter()
        rows = ask(s, cmd)
        dt = (time.perf_counter() - t0) * 1000
        if not rows:
            print(f"    [无回包] 等了 {dt:.0f} ms")
        else:
            for r in rows:
                print(f"    {r}")
        print()

    s.close()


if __name__ == "__main__":
    main()
