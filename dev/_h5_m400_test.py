"""验证 M400 是否排队等待。

关键看**回码的先后顺序**：
  先发 G1（4 秒运动），50ms 后发 M400。

  如果 M400 排队     → 先收到 E000（G1 跑完，约 4 秒），再收到 B000（约 4.05 秒）
  如果 M400 不排队   → B000 几乎立刻就到，比 E000 早
"""

from __future__ import annotations

import socket
import time

IP = "192.168.0.102"
PORT = 5000
LOCAL = "192.168.0.100"
CR = b"\r"
LETTERS = ("E", "B")


def is_code(t: str) -> bool:
    return len(t) == 4 and t[0] in LETTERS and t[1:].isdigit()


def clear(s: socket.socket) -> None:
    s.settimeout(0.2)
    try:
        while True:
            s.recvfrom(4096)
    except socket.timeout:
        pass


def main() -> None:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind((LOCAL, 0))

    print("=" * 60)
    print(" 验证 M400 是否排队等待")
    print("=" * 60)

    clear(s)
    s.sendto(b"G1 X0 F600" + CR, (IP, PORT))
    time.sleep(3)
    clear(s)

    print("\n发 G1 X20 F300（约 4 秒），50ms 后发 M400")
    s.sendto(b"G1 X20 F300" + CR, (IP, PORT))
    time.sleep(0.05)
    t0 = time.perf_counter()
    s.sendto(b"M400" + CR, (IP, PORT))

    events: list[tuple[str, float]] = []
    s.settimeout(0.3)
    while time.perf_counter() - t0 < 25:
        try:
            data, _ = s.recvfrom(4096)
        except socket.timeout:
            if len(events) >= 2:
                break
            continue
        for part in data.decode("ascii", "replace").split("\r"):
            part = part.strip()
            if is_code(part):
                t = time.perf_counter() - t0
                if not events or events[-1][0] != part or t - events[-1][1] > 0.05:
                    events.append((part, t))

    print("\n  回码到达顺序：")
    for code, t in events:
        print(f"    {t:6.2f} s   {code}")

    print()
    if len(events) >= 2:
        first, second = events[0], events[1]
        if first[0] == "E000" and second[0] == "B000" and first[1] > 3.0:
            print("  ✓ 排队生效：E000 等了约 4 秒（G1 跑完），B000 紧随其后")
            print("    → 中止后用 M400 排空队列可靠")
        elif first[0] == "B000" and first[1] < 1.0:
            print("  ✗ M400 没排队：B000 立刻返回，不能用它等前面的运动")
        else:
            print(f"  顺序异常：{first} → {second}，需要人工判断")
    else:
        print("  回码不足两个，无法判断")

    clear(s)
    s.sendto(b"G1 X0 F600" + CR, (IP, PORT))
    time.sleep(3)
    s.close()


if __name__ == "__main__":
    main()
