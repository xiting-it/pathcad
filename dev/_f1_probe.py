"""LCSystemF1 串口协议联调测试。

协议要点（见 _proto_f1.txt）：
  - 指令帧：@<正文>*<CRC16 4位HEX>\\r，整帧（不含 CR）最长 95 字节
  - 应答帧：E<2位错误码>*<CRC16>\\r，固定 9 字节
  - 状态帧：D L<3位> R<3位> P<x> <y> <z>*<CRC16>\\r（仅 M50）
  - CRC16-CCITT-FALSE，初值 0xFFFF，多项式 0x1021，不反射不异或
  - 严格一问一答，运动类指令跑完才应答
  - 调试旁路：CRC 填 FFFF 可跳过校验
"""

from __future__ import annotations

import serial
import time

PORT = "COM7"
BAUD = 115200


def crc16_ccitt_false(data: bytes) -> int:
    crc = 0xFFFF
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if (crc & 0x8000) else (crc << 1) & 0xFFFF
    return crc


def build_cmd(gcode: str, bypass_crc: bool = False) -> bytes:
    body = gcode.encode("ascii")
    if len(body) > 89:
        raise ValueError(f"正文 {len(body)}B 超过 89B 上限")
    crc = "FFFF" if bypass_crc else "%04X" % crc16_ccitt_false(body)
    return b"@" + body + b"*" + crc.encode("ascii") + b"\r"


def parse_resp(frame: bytes) -> tuple[str, bool, str]:
    """解析应答帧。返回 (错误码或全文, CRC是否正确, 原始内容)。"""
    raw = frame.decode("ascii", errors="replace")
    if frame[:1] == b"D":
        # 状态帧
        try:
            head, crc_hex = frame.split(b"*")
            ok = int(crc_hex, 16) == crc16_ccitt_false(head)
        except Exception:
            ok = False
        return raw, ok, raw
    if b"*" not in frame:
        return raw, False, raw
    head, crc_hex = frame.split(b"*", 1)
    try:
        ok = int(crc_hex.decode("ascii"), 16) == crc16_ccitt_false(head)
    except Exception:
        ok = False
    code = head.decode("ascii", errors="replace")
    return code, ok, raw


def send(ser, gcode: str, timeout: float = 5.0, bypass: bool = False):
    frame = build_cmd(gcode, bypass_crc=bypass)
    ser.reset_input_buffer()
    ser.write(frame)
    ser.flush()
    t0 = time.time()
    ser.timeout = timeout
    data = ser.read_until(b"\r")
    dt = time.time() - t0
    if not data:
        return None, dt, frame
    return data[:-1] if data.endswith(b"\r") else data, dt, frame


def main():
    print(f"打开 {PORT} @ {BAUD} …")
    try:
        ser = serial.Serial(PORT, BAUD, timeout=5, write_timeout=5)
    except Exception as e:
        print(f"[FAIL] 串口打不开：{e}")
        return 1

    time.sleep(0.3)
    ser.reset_input_buffer()
    print("已打开\n")

    tests = [
        ("M50", "状态查询", 3.0),
        ("G28 X", "X 轴回零", 60.0),
    ]

    for gcode, desc, timeout in tests:
        print(f"── {desc}: {gcode} ──")
        resp, dt, frame = send(ser, gcode, timeout=timeout)
        print(f"  发送帧: {frame!r}")
        if resp is None:
            print(f"  [超时] {dt:.2f}s 内无应答")
        else:
            code, ok, raw = parse_resp(resp)
            print(f"  应答: {raw!r}")
            print(f"  解析: code={code}, CRC={'OK' if ok else 'BAD'}, 耗时 {dt:.2f}s")
        print()

    ser.close()
    print("串口已关闭")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
