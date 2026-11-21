"""LCSystemF1 串口协议实现（STM32F103C8 三轴控制板）。

协议要点（v1.6）：
  指令帧  @<正文>*<CRC16 4位大写HEX>\\r     正文最长 89B
  应答帧  E<2位错误码>*<CRC16>\\r             固定 9 字节
  状态帧  D L<xyz> R<xyz> P<x> <y> <z>*<CRC16>\\r   仅 M50
  CRC     CRC16-CCITT-FALSE，初值 0xFFFF、多项式 0x1021、不反射不异或
          指令帧覆盖 @ 后 * 前的正文；应答帧覆盖 E+2位码 共 3 字节
  旁路    指令帧 CRC 填 FFFF 时跳过校验（开发调试用）

指令集：G00 / G01 / G02 / G28 / M50 —— 只有这五个。
没有 G92（无法软件清零）、没有暂停、没有回抽。

实测行为（2026-11-16，COM7 空载）：
  - 无加减速，耗时精确等于「距离 ÷ 速度」
  - 短线段无额外开销，可自由切段
  - G02 圆弧精度完美但比直线离散慢 2.7 倍 → 不用
  - 严格一问一答，运动期间发指令回 E07
"""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass, field

from .logger import SRC_ERR, SRC_JOB, SRC_UDP_RX, SRC_UDP_TX, get_logger

log = get_logger()

# ── 常量 ────────────────────────────────────────────────

MAX_BODY = 89           # 正文最长 89 字节（整帧 95 不含 CR）
DEFAULT_BAUD = 115200
BYPASS_CRC = "FFFF"

ERROR_CODES: dict[str, str] = {
    "E00": "成功",
    "E01": "CRC 错误",
    "E02": "帧格式错误",
    "E03": "不支持的指令",
    "E04": "语法/数值错误",
    "E05": "参数超范围",
    "E06": "圆弧参数错误",
    "E07": "系统忙（上一条运动未完成）",
    "E08": "限位触发",
    "E09": "回原点失败",
    "E10": "帧超长",
    "E11": "运动被中止",
}


# ── CRC ────────────────────────────────────────────────

def crc16_ccitt_false(data: bytes) -> int:
    """CRC16-CCITT-FALSE：初值 0xFFFF，多项式 0x1021，不反射不异或。"""
    crc = 0xFFFF
    for b in data:
        crc ^= b << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if (crc & 0x8000) else (crc << 1) & 0xFFFF
    return crc


def build_frame(gcode: str, bypass_crc: bool = False) -> bytes:
    """构造指令帧：@<正文>*<CRC>\\r"""
    body = gcode.encode("ascii")
    if len(body) > MAX_BODY:
        raise ValueError(f"正文 {len(body)}B 超过 {MAX_BODY}B 上限")
    crc = BYPASS_CRC if bypass_crc else "%04X" % crc16_ccitt_false(body)
    return b"@" + body + b"*" + crc.encode("ascii") + b"\r"


@dataclass
class Response:
    """解析后的应答。"""
    raw: str = ""
    code: str = ""          # "E00" / "E07" / "" 或 "D"
    ok: bool = False
    crc_ok: bool = True
    is_status: bool = False
    # 状态帧字段
    limits_left: tuple[int, int, int] = (0, 0, 0)
    limits_right: tuple[int, int, int] = (0, 0, 0)
    pos: tuple[float, float, float] = (0.0, 0.0, 0.0)
    elapsed: float = 0.0

    @property
    def desc(self) -> str:
        return ERROR_CODES.get(self.code, self.code or "未知")


_STATUS_RE = re.compile(r"^D\s+L(\d)(\d)(\d)\s+R(\d)(\d)(\d)\s+P\s*(.+)$")


def parse_response(frame: str) -> Response:
    """解析应答帧（已去掉尾部 \\r）。"""
    r = Response(raw=frame)
    if not frame:
        return r

    body, _, crc_hex = frame.partition("*")
    if crc_hex:
        try:
            r.crc_ok = int(crc_hex, 16) == crc16_ccitt_false(body.encode("ascii"))
        except ValueError:
            r.crc_ok = False

    if body.startswith("D"):
        r.is_status = True
        m = _STATUS_RE.match(body)
        if m:
            r.limits_left = (int(m.group(1)), int(m.group(2)), int(m.group(3)))
            r.limits_right = (int(m.group(4)), int(m.group(5)), int(m.group(6)))
            try:
                xyz = m.group(7).split()
                r.pos = (float(xyz[0]), float(xyz[1]), float(xyz[2]))
            except (IndexError, ValueError):
                pass
            r.ok = True
            r.code = "D"
        return r

    if body.startswith("E") and len(body) >= 3:
        r.code = body[:3]
        r.ok = r.code == "E00"
    return r


# ── 连接 ────────────────────────────────────────────────

@dataclass
class SerialState:
    connected: bool = False
    port: str = ""
    baud: int = DEFAULT_BAUD
    sent: int = 0
    ok: int = 0
    failed: int = 0
    last_error: str = ""
    pos: tuple[float, float, float] = (0.0, 0.0, 0.0)
    limits_left: tuple[int, int, int] = (0, 0, 0)
    limits_right: tuple[int, int, int] = (0, 0, 0)
    lock: threading.Lock = field(default_factory=threading.Lock)


class LCSerialLink:
    """串口连接：发一条等一条。"""

    def __init__(self, port: str = "COM7", baud: int = DEFAULT_BAUD):
        self.state = SerialState(port=port, baud=baud)
        self.ser = None
        self._io_lock = threading.Lock()
        self.bypass_crc = True   # 默认用旁路，生产环境可关

    # ── 生命周期 ──

    def open(self) -> tuple[bool, str]:
        try:
            import serial
        except ImportError:
            return False, "缺少 pyserial，请先 pip install pyserial"

        try:
            self.ser = serial.Serial(
                self.state.port, self.state.baud, timeout=3, write_timeout=5
            )
            time.sleep(0.4)
            self.ser.reset_input_buffer()
            self.state.connected = True
            log.info(SRC_UDP_TX, f"串口已打开 {self.state.port} @ {self.state.baud}")
        except Exception as e:
            msg = f"串口打不开 {self.state.port}：{e}"
            log.error(SRC_ERR, msg)
            return False, msg

        # 探活
        r = self.ask("M50", timeout=3.0)
        if r.is_status:
            msg = f"已连接 {self.state.port}，位置 ({r.pos[0]:g}, {r.pos[1]:g}, {r.pos[2]:g})"
            log.info(SRC_UDP_RX, msg)
            return True, msg
        if r.code:
            msg = f"已连接 {self.state.port}，M50 返回 {r.code}（{r.desc}）"
            log.warn(SRC_UDP_RX, msg)
            return True, msg
        msg = f"串口开了但 {self.state.port} 无应答，检查板子是否上电、端口是否正确"
        log.error(SRC_ERR, msg)
        return False, msg

    def close(self):
        try:
            if self.ser:
                self.ser.close()
                log.info(SRC_UDP_TX, f"串口已关闭 {self.state.port}")
        except Exception:
            pass
        self.ser = None
        self.state.connected = False

    # ── 收发 ──

    def ask(self, gcode: str, timeout: float = 5.0) -> Response:
        """发一行，等应答。"""
        if not self.ser:
            return Response(raw="(未连接)", code="")

        try:
            frame = build_frame(gcode, bypass_crc=self.bypass_crc)
        except ValueError as e:
            log.error(SRC_ERR, str(e))
            return Response(raw=f"(超长) {e}", code="E10")

        with self._io_lock:
            try:
                self.ser.reset_input_buffer()
                self.ser.write(frame)
                self.ser.flush()
            except Exception as e:
                log.error(SRC_ERR, f"串口写入失败：{e}")
                self.state.last_error = str(e)
                return Response(raw=f"(写失败) {e}")

            log.debug(SRC_UDP_TX, gcode, raw=frame)
            self.state.sent += 1

            t0 = time.time()
            self.ser.timeout = timeout
            try:
                data = self.ser.read_until(b"\r")
            except Exception as e:
                log.error(SRC_ERR, f"串口读取失败：{e}")
                return Response(raw=f"(读失败) {e}")

            elapsed = time.time() - t0

        if not data:
            log.warn(SRC_UDP_RX, f"应答超时（{timeout:g}s）：{gcode[:40]}")
            self.state.failed += 1
            r = Response(raw="(超时)", elapsed=elapsed)
            return r

        text = data[:-1].decode("ascii", errors="replace") if data.endswith(b"\r") else data.decode("ascii", errors="replace")
        log.debug(SRC_UDP_RX, text, raw=data)
        r = parse_response(text)
        r.elapsed = elapsed

        with self.state.lock:
            if r.ok:
                self.state.ok += 1
            else:
                self.state.failed += 1
                self.state.last_error = r.code or "超时"
            if r.is_status:
                self.state.pos = r.pos
                self.state.limits_left = r.limits_left
                self.state.limits_right = r.limits_right

        if not r.ok and r.code:
            log.warn(SRC_UDP_RX, f"{r.code} {r.desc}  ← {gcode[:40]}")
        if not r.crc_ok:
            log.warn(SRC_ERR, f"应答 CRC 校验失败：{text}")

        return r

    def query_status(self) -> Response:
        return self.ask("M50", timeout=3.0)

    # ── 快照 ──

    def snapshot(self) -> dict:
        st = self.state
        with st.lock:
            return {
                "connected": st.connected,
                "port": st.port,
                "baud": st.baud,
                "sent": st.sent,
                "ok": st.ok,
                "failed": st.failed,
                "last_error": st.last_error,
                "pos": [round(v, 3) for v in st.pos],
                "limits_left": list(st.limits_left),
                "limits_right": list(st.limits_right),
            }


# ── 打印任务 ──────────────────────────────────────────────

@dataclass
class SerialJobState:
    running: bool = False
    total: int = 0
    done: int = 0
    errors: int = 0
    current: str = ""
    message: str = "空闲"
    started_at: float = 0.0
    finished_at: float = 0.0
    aborted: bool = False
    log: list[str] = field(default_factory=list)
    lock: threading.Lock = field(default_factory=threading.Lock)


class SerialJob:
    """逐行下发 G-code。一发一等。"""

    def __init__(self, link: LCSerialLink):
        self.link = link
        self.state = SerialJobState()
        self.thread: threading.Thread | None = None
        self._stop = threading.Event()

    def start(self, gcode: str) -> tuple[bool, str]:
        if self.state.running:
            return False, "已经在发送了"

        lines: list[str] = []
        for raw in gcode.splitlines():
            s = raw.split(";")[0].strip()
            if not s:
                continue
            if len(s.encode("ascii", errors="replace")) > MAX_BODY:
                return False, f"有行超过 {MAX_BODY} 字节：{s[:40]}…"
            lines.append(s)

        if not lines:
            return False, "没有可发送的指令"

        self.state = SerialJobState(running=True, total=len(lines), started_at=time.time())
        self._stop.clear()
        log.info(SRC_JOB, f"开始发送 {len(lines)} 行 → {self.link.state.port}")
        self.thread = threading.Thread(target=self._run, args=(lines,), daemon=True)
        self.thread.start()
        return True, f"开始发送 {len(lines)} 行"

    def stop(self):
        self._stop.set()
        self.state.aborted = True
        log.warn(SRC_JOB, "收到中止请求")

    def _run(self, lines: list[str]):
        st = self.state
        last_report = 0

        for i, line in enumerate(lines):
            if self._stop.is_set():
                st.message = f"已中止（第 {i}/{st.total} 行）"
                log.warn(SRC_JOB, st.message)
                break

            st.current = line

            # 运动类指令要等它真跑完，超时给足
            up = line.upper()
            timeout = 30.0
            if up.startswith(("G00", "G01", "G02", "G28")):
                timeout = 120.0

            r = self.link.ask(line, timeout=timeout)

            if r.ok:
                st.done += 1
                if st.done - last_report >= 50:
                    last_report = st.done
                    pct = st.done * 100 // max(1, st.total)
                    log.info(SRC_JOB, f"进度 {st.done}/{st.total}（{pct}%）")
            else:
                st.errors += 1
                msg = f"第 {i + 1} 行 {r.code or '超时'} | {line[:40]}"
                st.log.append(msg)
                if len(st.log) > 200:
                    st.log.pop(0)
                log.error(SRC_ERR, msg)

                # 限位触发 / 回零失败 → 直接停
                if r.code in ("E08", "E09", "E11"):
                    st.message = f"中止：{r.code}（{r.desc}）"
                    log.error(SRC_ERR, st.message)
                    break

        st.running = False
        st.finished_at = time.time()
        if not st.aborted:
            elapsed = st.finished_at - st.started_at
            st.message = (
                f"完成：{st.done}/{st.total} 行成功，{st.errors} 个错误"
                if st.errors else f"完成：{st.done} 行全部成功"
            )
            (log.error if st.errors else log.info)(SRC_JOB, f"{st.message}（耗时 {elapsed:.1f}s）")

    def snapshot(self) -> dict:
        st = self.state
        elapsed = 0.0
        if st.started_at:
            elapsed = (st.finished_at or time.time()) - st.started_at
        return {
            "running": st.running,
            "total": st.total,
            "done": st.done,
            "errors": st.errors,
            "current": st.current,
            "message": st.message,
            "progress": (st.done / st.total) if st.total else 0.0,
            "elapsed": round(elapsed, 1),
            "log": st.log[-30:],
        }


# ── 单例 ────────────────────────────────────────────────

_link: LCSerialLink | None = None
_job: SerialJob | None = None
_lock = threading.Lock()


def get_serial_link(port: str = "COM7", baud: int = DEFAULT_BAUD) -> LCSerialLink:
    global _link, _job
    with _lock:
        if _link is None or _link.state.port != port or _link.state.baud != baud:
            if _link and _link.state.connected:
                _link.close()
            _link = LCSerialLink(port, baud)
            _job = SerialJob(_link)
        return _link


def get_serial_job(port: str = "COM7", baud: int = DEFAULT_BAUD) -> SerialJob:
    get_serial_link(port, baud)
    return _job  # type: ignore[return-value]


def _clean_desc(desc: str) -> str:
    """修串口描述里的乱码。

    pyserial 在 Windows 上通过注册表/WMI 取 FriendlyName，
    非 ASCII 字符（中文的"蓝牙链接上的标准串行"）可能被按错误编码解出来，
    变成 U+9483 这类无意义汉字。

    做法：如果描述里出现「大量生僻汉字且没有可读词」，就退回到
    只用 hwid 判断，描述换成通用文字。
    """
    if not desc:
        return ""
    # 常见可读描述直接放行
    readable = ("CH340", "CP210", "FTDI", "USB-SERIAL", "USB 串行", "蓝牙",
                "Bluetooth", "Standard", "Serial")
    if any(k in desc for k in readable):
        return desc
    # 出现生僻字（CJK 统一表意文字里的高频字之外）就判定为乱码
    weird = sum(1 for ch in desc if "\u4e00" <= ch <= "\u9fff")
    if weird >= 3:
        return "串行端口"
    return desc


def list_serial_ports() -> list[dict]:
    """列出可用串口。"""
    out: list[dict] = []
    try:
        from serial.tools import list_ports
        for p in list_ports.comports():
            desc = _clean_desc(p.description or "")
            hwid = p.hwid or ""
            blob = (desc + " " + hwid).upper()
            out.append({
                "device": p.device,
                "description": desc,
                "hwid": hwid,
                # CH340 / CP210x / FTDI 这类 USB 转串口更可能是我们的板子
                "likely_board": any(k in blob for k in ("CH340", "CP210", "FTDI", "USB-SERIAL")),
            })
    except ImportError:
        pass
    except Exception:
        pass
    return out
