"""重启后的关键验证：G4 到底能不能用，IOOUT 掉电默认值。

测试顺序很重要 —— 把 G4 放到最后，万一它又把队列卡死，
前面该收的数据已经收完了。
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

    def drain(self, wait: float = 0.2) -> None:
        end = time.time() + wait
        self.s.settimeout(wait)
        try:
            while True:
                self.s.recvfrom(4096)
                if time.time() > end:
                    break
        except socket.timeout:
            pass

    def ask(self, line: str, timeout: float = 5.0) -> tuple[str, list[str], float]:
        self.drain(0.15)
        self.s.settimeout(timeout)
        self.s.sendto(line.encode() + CR, (IP, PORT))
        rows: list[str] = []
        t0 = time.perf_counter()
        code = "无回包"
        while time.perf_counter() - t0 < timeout:
            try:
                data, _ = self.s.recvfrom(4096)
            except socket.timeout:
                break
            for part in data.decode("ascii", "replace").split("\r"):
                part = part.strip()
                if not part:
                    continue
                rows.append(part)
                if len(part) >= 4 and part[0] in "EB" and part[1:4].isdigit():
                    code = part
                    break
            if code != "无回包":
                break
        dt = (time.perf_counter() - t0) * 1000
        data_lines = [
            r for r in rows
            if not r.startswith("queued") and r != "ok"
            and not (len(r) >= 4 and r[0] in "EB" and r[1:4].isdigit())
        ]
        return code, data_lines, dt


def step(h: H5, cmd: str, note: str = "", timeout: float = 5.0) -> str:
    code, data, dt = h.ask(cmd, timeout)
    flag = "OK " if code.endswith("000") else "!! "
    print(f"  {flag}{cmd:16s} → {code:6s} {dt:7.0f} ms   {' '.join(data)[:70]}{note}")
    return code


def main() -> None:
    h = H5()
    print("=" * 66)
    print(" 重启后验证（G4 放最后，避免再次卡死影响前面的数据）")
    print("=" * 66)

    print("\n【1】掉电默认状态 —— 安全项")
    step(h, "M120", "  ← IOOUT 应为 0x00（全关）")

    print("\n【2】队列是否清空")
    step(h, "M114", "  ← 应回 B000 + 四轴坐标")
    step(h, "M400", "  ← 等运动停止应立刻成功")

    print("\n【3】运动是否正常")
    step(h, "G1 X1 F600")
    step(h, "M114", "  ← X 应变成 1.000 附近")

    print("\n【4】★ 关键测试：G4 P100（不带 P0）")
    code = step(h, "G4 P100", "  ← 这是文档说支持的写法和参数", timeout=8)

    print("\n【5】G4 之后队列还活着吗")
    if code.endswith("000"):
        print("      G4 P100 成功，继续测其他延时")
        step(h, "G4 P300", timeout=8)
        step(h, "G4 S0.2", "  ← 秒为单位", timeout=8)
        step(h, "G1 X2 F600", "  ← 运动应正常")
    else:
        print("      G4 失败，检查队列状态")
    step(h, "M114", "  ← 若回 E020 说明队列已满")
    step(h, "G1 X2 F600")

    print("\n【6】最后确认 IO 快通道仍然可用")
    step(h, "M120")
    step(h, "M121 V0")

    h.close()
    print("\n完成。")


if __name__ == "__main__":
    main()
