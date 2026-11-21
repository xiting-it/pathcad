"""统一日志：控制台（关键事件）+ 环形缓冲（供 Web 面板）+ 文件（data/log/）。

设计要点：
- 一条日志只产生一次，三个去处共用同一条记录，避免格式不一致。
- 控制台只打「关键事件」：启动、连接、任务开始/结束、错误、每 N 条进度。
  逐条指令的收发明细只进环形缓冲和文件，不刷屏。
- 环形缓冲给 Web 面板做增量拉取（按 seq），刷新页面也能续上。
- 文件按天切，放在 exe 同目录的 data/log/ 下。
"""

from __future__ import annotations

import os
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

# ── 来源标签 ────────────────────────────────────────────

SRC_UDP_TX = "UDP→"
SRC_UDP_RX = "UDP←"
SRC_JOB = "JOB"
SRC_MOCK = "MOCK"
SRC_HTTP = "HTTP"
SRC_SYS = "SYS"
SRC_ERR = "ERR"

LEVELS = ("debug", "info", "warn", "error")


@dataclass
class Entry:
    seq: int
    ts: float
    level: str
    source: str
    message: str
    raw: bytes | None = None  # 原始字节，HEX 视图用

    def time_str(self) -> str:
        return datetime.fromtimestamp(self.ts).strftime("%H:%M:%S.%f")[:-3]

    def to_dict(self) -> dict:
        d = {
            "seq": self.seq,
            "t": self.time_str(),
            "level": self.level,
            "src": self.source,
            "msg": self.message,
        }
        if self.raw is not None:
            d["hex"] = self.raw.hex(" ").upper()
        return d


def _setup_console_encoding():
    """让 Windows 控制台能正确显示中文。

    控制台默认代码页是 936（GBK），Python 按 cp936 输出，
    一旦遇到 UTF-8 里的字符就会乱码或抛 UnicodeEncodeError。
    做法：把代码页切到 65001（UTF-8），再把 stdout/stderr 的编码
    强制成 utf-8。非 Windows 或非控制台环境直接跳过。
    """
    if os.name != "nt":
        return
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        # 只有真的连着控制台才切，避免管道/重定向时改到别人
        if kernel32.GetConsoleWindow():
            kernel32.SetConsoleOutputCP(65001)
            kernel32.SetConsoleCP(65001)
    except Exception:
        pass
    for stream_name in ("stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        if stream is None:
            continue
        try:
            if stream.encoding and stream.encoding.lower().replace("-", "") != "utf8":
                stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


class Logger:
    """全局日志器。线程安全。"""

    def __init__(self, capacity: int = 2000):
        self._buf: deque[Entry] = deque(maxlen=capacity)
        self._seq = 0
        self._lock = threading.Lock()
        self._file_path: Path | None = None
        self._file = None
        self._file_day = ""
        self._log_dir: Path | None = None
        self.file_enabled = True
        self.console_enabled = True
        # 控制台只打这些级别（info 以上）；debug 只进缓冲和文件
        self.console_min_level = "info"
        _setup_console_encoding()

    # ── 配置 ──

    def set_log_dir(self, root: Path) -> Path:
        """设置日志目录为 <root>/data/log，返回实际路径。"""
        d = Path(root) / "data" / "log"
        d.mkdir(parents=True, exist_ok=True)
        self._log_dir = d
        return d

    def _ensure_file(self):
        if not self.file_enabled:
            return
        d = getattr(self, "_log_dir", None)
        if d is None:
            return
        today = datetime.now().strftime("%Y%m%d")
        if self._file is not None and self._file_day == today:
            return
        # 跨天或首次打开
        if self._file is not None:
            try:
                self._file.close()
            except Exception:
                pass
            self._file = None
        path = Path(d) / f"{today}.txt"
        try:
            self._file = open(path, "a", encoding="utf-8", buffering=1)
            self._file_day = today
            self._file_path = path
        except Exception as e:
            self._file = None
            if self.console_enabled:
                print(f"[WARN] 日志文件打开失败：{e}", flush=True)

    # ── 写日志 ──

    def log(self, level: str, source: str, message: str, raw: bytes | None = None) -> Entry:
        if level not in LEVELS:
            level = "info"
        with self._lock:
            self._seq += 1
            e = Entry(seq=self._seq, ts=time.time(), level=level,
                      source=source, message=message, raw=raw)
            self._buf.append(e)

        # 控制台：只看关键
        if self.console_enabled and self._console_wants(level):
            try:
                print(f"{e.time_str()} [{source}] {message}", flush=True)
            except Exception:
                pass

        # 文件：全部
        if self.file_enabled:
            with self._lock:
                self._ensure_file()
                if self._file is not None:
                    try:
                        hexpart = f"  |hex| {raw.hex(' ').upper()}" if raw else ""
                        self._file.write(
                            f"{datetime.fromtimestamp(e.ts).strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]} "
                            f"[{level.upper():5s}] [{source}] {message}{hexpart}\n"
                        )
                    except Exception:
                        pass
        return e

    def _console_wants(self, level: str) -> bool:
        order = {"debug": 0, "info": 1, "warn": 2, "error": 3}
        return order.get(level, 1) >= order.get(self.console_min_level, 1)

    # ── 便捷方法 ──

    def debug(self, src, msg, raw=None):
        return self.log("debug", src, msg, raw)

    def info(self, src, msg, raw=None):
        return self.log("info", src, msg, raw)

    def warn(self, src, msg, raw=None):
        return self.log("warn", src, msg, raw)

    def error(self, src, msg, raw=None):
        return self.log("error", src, msg, raw)

    # ── 读取 ──

    def since(self, seq: int = 0, limit: int = 800) -> dict:
        """返回 seq 之后的日志（最多 limit 条）。"""
        with self._lock:
            items = [e for e in self._buf if e.seq > seq]
            total_buffered = len(self._buf)
            latest = self._seq
            oldest = self._buf[0].seq if self._buf else 0
        # 只取最新的 limit 条
        truncated = len(items) > limit
        if truncated:
            items = items[-limit:]
        return {
            "entries": [e.to_dict() for e in items],
            "latest": latest,
            "oldest": oldest,
            "buffered": total_buffered,
            "truncated": truncated,
            "file": str(self._file_path) if self._file_path else None,
        }

    def clear(self):
        with self._lock:
            self._buf.clear()


# ── 单例 ────────────────────────────────────────────────

_logger: Logger | None = None
_lock = threading.Lock()


def get_logger() -> Logger:
    global _logger
    with _lock:
        if _logger is None:
            _logger = Logger()
        return _logger


def init(root: Path, console: bool = True, file: bool = True) -> Logger:
    """初始化日志：设置文件目录、开关。"""
    lg = get_logger()
    lg.console_enabled = console
    lg.file_enabled = file
    if file:
        p = lg.set_log_dir(root)
        lg.info(SRC_SYS, f"日志文件目录：{p}")
    return lg
